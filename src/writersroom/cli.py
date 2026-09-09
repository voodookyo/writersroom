"""命令行入口（docs/design/spec.md §7）：argparse 单入口 ``writersroom``。

- 退出码：0 成功；2 用户/环境错误（WritersRoomError → stderr 打印 ``e.render()``）；
  1 内部错误（意外异常）。
- ``--json``（根命令或任意子命令之后均可）：把主要返回 dict 以 JSON 打印到 stdout，
  便于脚本化；默认输出为简洁中文。
- stage/llm 两组命令对并行开发的 ``writersroom.stages`` / ``writersroom.llm`` 做
  import 保护：模块缺失时报「模块未就绪」（WritersRoomError，exit 2），不伪造成功。

集成备注（stages/llm 模块落地后若接口不一致，只需调整本文件的调用点）：
    stages.list_pipelines() -> list[str | dict]
    stages.list_stages(pipeline) -> list[str | dict]
    stages.run_stage(ws, pipeline, stage, use_llm=False, extra="", llm_fn=None) -> Path | dict
    stages.confirm_stage(ws, pipeline, stage, run_id)
    stages.stage_history(ws, pipeline, stage) -> list[dict]
    llm.resolve_config(base_url=None, api_key=None, model=None) -> dict | None
    llm.check(cfg)（失败抛 WritersRoomError）
    llm.chat(cfg, system, user) -> str
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

from . import analyze, compare, docgen, importer, ingest, kb, meetings, memory, project, scenes
from .core import WritersRoomError, Workspace, read_json

# stage lock/unlock 的锁定项（与 project.LOCK_KEYS 一致，帮助文本用）
_LOCK_KEYS = "、".join(project.LOCK_KEYS)

_ISSUE_LABELS = (("new", "新增"), ("removed", "移除"), ("improved", "改善"),
                 ("regressed", "恶化"), ("persistent", "持续"))


# ---------- 输出辅助 ----------

def _want_json(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "json", False))


def _say(args: argparse.Namespace, text: str) -> None:
    """人类可读输出；--json 模式下静默（stdout 只留 JSON）。"""
    if not _want_json(args):
        print(text)


def _split_csv(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[，,]", value) if p.strip()]


def _latest_dir(base: Path) -> Path | None:
    if not base.is_dir():
        return None
    dirs = sorted(p for p in base.iterdir() if p.is_dir())
    return dirs[-1] if dirs else None


def _require_stages():
    try:
        from . import stages
        return stages
    except ImportError as e:
        raise WritersRoomError(
            "stages 模块未就绪（阶段运行器尚未集成）",
            "该模块由并行任务开发中；集成完成后再运行 writersroom stage …",
        ) from e


def _require_llm():
    try:
        from . import llm
        return llm
    except ImportError as e:
        raise WritersRoomError(
            "llm 模块未就绪（LLM 适配层尚未集成）",
            "该模块由并行任务开发中；集成完成后再运行 writersroom llm … / stage run --use-llm",
        ) from e


# ---------- 各命令处理器（打印中文摘要，返回主要 dict） ----------

def _cmd_init(args) -> dict:
    existed = Path(args.dir).joinpath("project.json").exists()
    ws = Workspace.init(args.dir, title=args.title or "")
    if existed:
        _say(args, f"工作区已存在（project.json 未改动）：{ws.root}")
    else:
        _say(args, f"已初始化工作区：{ws.root}")
    return {"workspace": str(ws.root), "title": args.title or ""}


def _cmd_ingest(args) -> dict:
    ws = Workspace(args.ws)
    source_id, doc = ingest.ingest(ws, args.file, source_id=args.source_id, fmt=args.format)
    warnings = doc.get("warnings", [])
    lines = [
        f"已导入：{source_id}（格式 {doc['source']['format']}，{len(doc['blocks'])} 块）",
        f"规范化文档：normalized/{source_id}.json",
    ]
    if warnings:
        lines.append(f"警告（{len(warnings)} 条）：")
        lines += [f"- [{w['code']}] {w['detail']}" for w in warnings]
    else:
        lines.append("警告：无")
    _say(args, "\n".join(lines))
    return {"source_id": source_id, "format": doc["source"]["format"],
            "blocks": len(doc["blocks"]), "warnings": warnings}


def _cmd_analyze(args) -> dict:
    ws = Workspace(args.ws)
    run_dir = analyze.run_analysis(ws, args.version_label, tokenizer=args.tokenizer)
    _say(args, f"分析完成：{ws.rel(run_dir)}\n报告：{ws.rel(run_dir / 'report.md')}")
    return {"run_dir": ws.rel(run_dir), "report": ws.rel(run_dir / "report.md")}


def _cmd_report(args) -> dict:
    ws = Workspace(args.ws)
    ws.require()
    run = analyze.latest_run(ws, args.version_label)
    if run is None or not (run / "report.md").exists():
        raise WritersRoomError(
            f"版本 {args.version_label} 尚无分析报告",
            f"先运行 writersroom analyze {args.ws} {args.version_label}",
        )
    _say(args, ws.rel(run / "report.md"))
    return {"run_dir": ws.rel(run), "report": ws.rel(run / "report.md")}


def _cmd_version_add(args) -> dict:
    ws = Workspace(args.ws)
    entry = project.version_add(ws, args.source_id, args.label, note=args.note or "")
    _say(args, f"已登记版本：{entry['label']} → {entry['source_id']}")
    return entry


def _cmd_version_list(args) -> dict:
    ws = Workspace(args.ws)
    versions = project.version_list(ws)
    if versions:
        _say(args, "\n".join(
            f"{v['label']}\t{v['source_id']}\t{v.get('note') or ''}" for v in versions))
    else:
        _say(args, "（尚未登记版本）")
    return {"versions": versions}


def _cmd_profile(args) -> dict:
    ws = Workspace(args.ws)
    fields: dict = {}
    for key in ("title", "format", "audience", "platform", "logline", "status"):
        value = getattr(args, key)
        if value is not None:
            fields[key] = value
    for key in ("genre", "goals"):
        value = getattr(args, key)
        if value is not None:
            fields[key] = _split_csv(value)
    if args.constraints is not None:
        try:
            constraints = json.loads(args.constraints)
        except json.JSONDecodeError as e:
            raise WritersRoomError(
                f"--constraints 不是合法 JSON：{e}",
                '示例：--constraints \'{"episodes": 12, "page_chars": 500}\'',
            ) from e
        if not isinstance(constraints, dict):
            raise WritersRoomError(
                "--constraints 须为 JSON 对象",
                '示例：--constraints \'{"episodes": 12}\'',
            )
        fields["constraints"] = constraints
    if not fields:
        raise WritersRoomError(
            "未提供任何档案字段",
            "至少给一个旗标：--title/--format/--genre/--audience/--platform/"
            "--logline/--goals/--constraints/--status",
        )
    proj = project.set_profile(ws, **fields)
    _say(args, f"已更新项目档案（{', '.join(fields)}）")
    return proj


def _cmd_compare(args) -> dict:
    ws = Workspace(args.ws)
    result = compare.compare(ws, args.a, args.b)
    base = ws.path("analysis", "compare", f"{args.a}__vs__{args.b}")
    latest = _latest_dir(base)
    md = ws.rel(latest / "compare.md") if latest else "(compare.md 路径未知)"
    counts = " / ".join(f"{label} {len(result['issues'][key])}" for key, label in _ISSUE_LABELS)
    _say(args, f"比较完成：{md}\n问题五分类：{counts}")
    return result


def _cmd_memory_add(args) -> dict:
    ws = Workspace(args.ws)
    row = memory.add_candidate(ws, args.kind, args.text, source=args.source or "")
    _say(args, f"已添加候选 {row['id']}（{row['kind']}）：{row['text']}")
    return row


def _render_memory_rows(rows: list[dict]) -> str:
    if not rows:
        return "（无候选记忆）"
    lines = []
    for r in rows:
        line = f"{r['id']} [{r['effective_status']}] {r['kind']}｜{r['text']}（来源 {r['source']}）"
        latest = r.get("latest_decision")
        if latest:
            line += f" ← 最新决定 {latest['id']}:{latest['action']}"
        lines.append(line)
    return "\n".join(lines)


def _cmd_memory_list(args) -> dict:
    ws = Workspace(args.ws)
    rows = memory.list_memory(ws, status=args.status)
    _say(args, _render_memory_rows(rows))
    return {"items": rows}


_DECISION_USAGE = ("用法：writersroom decision <ws> <confirm|propose|reject|park|question> "
                   "<target_id> [--text 文本]；或 writersroom decision list <ws>")


def _cmd_decision(args) -> dict:
    argv = list(args.argv)
    if argv and argv[0] == "list":
        if len(argv) != 2:
            raise WritersRoomError("decision list 只需要工作区路径", _DECISION_USAGE)
        rows = memory.list_memory(Workspace(argv[1]))
        _say(args, _render_memory_rows(rows))
        return {"items": rows}
    if len(argv) < 3:
        raise WritersRoomError("decision 参数不足（需要 <ws> <动作> <target_id>）", _DECISION_USAGE)
    ws_path, action, target_id = argv[0], argv[1], argv[2]
    if action not in memory.DECISION_ACTIONS:
        raise WritersRoomError(
            f"未知决定动作：{action}",
            f"动作只能是：{'/'.join(memory.DECISION_ACTIONS)}；{_DECISION_USAGE}",
        )
    row = memory.decide(Workspace(ws_path), target_id, action, text=args.text or "")
    _say(args, f"已记录决定 {row['id']}：{row['action']} → {row['target_id']}"
               f"（有效状态：{memory.ACTION_STATUS[row['action']]}）")
    return row


def _cmd_tasks_gen(args) -> dict:
    ws = Workspace(args.ws)
    doc = memory.gen_tasks(ws)
    lines = [f"已生成 {len(doc['tasks'])} 条任务建议 → tasks/tasks.json、tasks/tasks.md"]
    if doc.get("message"):
        lines.append(f"说明：{doc['message']}")
    _say(args, "\n".join(lines))
    return doc


def _cmd_tasks_list(args) -> dict:
    ws = Workspace(args.ws)
    ws.require()
    path = ws.path("tasks", "tasks.json")
    if not path.exists():
        raise WritersRoomError("尚未生成任务清单", "先运行 writersroom tasks gen")
    doc = read_json(path)
    tasks = doc.get("tasks", [])
    if tasks:
        lines = []
        for t in tasks:
            lines.append(f"{t['id']} [{t['status']}] {t['title']}")
            lines.append(f"    原因：{t['why']}｜来源：{t['source_ref']}")
        _say(args, "\n".join(lines))
    else:
        _say(args, "（无任务建议）")
    return doc


# ---------- meeting ----------

def _cmd_meeting_locate(args) -> dict:
    ws = Workspace(args.ws)
    mdir = meetings.locate(ws, args.date, args.type)
    _say(args, f"会议目录就绪：{mdir.name}（{ws.rel(mdir)}）")
    return {"meeting_id": mdir.name, "dir": ws.rel(mdir)}


def _cmd_meeting_list(args) -> dict:
    ws = Workspace(args.ws)
    rows = meetings.list_meetings(ws)
    if rows:
        _say(args, "\n".join(
            f"{r['meeting_id']}｜raw {'有' if r['raw'] else '无'}｜normalized "
            f"{'有' if r['normalized'] else '无'}｜runs {len(r['runs'])}｜handoff "
            f"{'有' if r['handoff'] else '无'}" for r in rows))
    else:
        _say(args, "（无会议）")
    return {"meetings": rows}


def _cmd_meeting_ingest(args) -> dict:
    ws = Workspace(args.ws)
    entry = meetings.ingest_meeting(ws, args.meeting_id, args.file)
    _say(args, f"已导入 {entry['name']}（{entry['format']}）→ {entry['path']}")
    return entry


def _cmd_meeting_normalize(args) -> dict:
    ws = Workspace(args.ws)
    doc = meetings.normalize(ws, args.meeting_id)
    _say(args, f"已规范化 {len(doc['turns'])} 条发言（警告 {len(doc['warnings'])} 条）"
               f"→ meetings/{args.meeting_id}/normalized.json")
    return doc


def _cmd_meeting_compact(args) -> dict:
    ws = Workspace(args.ws)
    run_dir = meetings.compact(ws, args.meeting_id)
    handoff = ws.path("meetings", args.meeting_id, "handoff.md")
    _say(args, f"已生成压缩：{ws.rel(run_dir)}\n交接：{ws.rel(handoff)}")
    return {"run_dir": ws.rel(run_dir), "handoff": ws.rel(handoff)}


def _cmd_meeting_review_rename(args) -> dict:
    ws = Workspace(args.ws)
    overrides = meetings.review(ws, args.meeting_id, "rename_speaker",
                                old=args.old, new=args.new)
    _say(args, f"已记录说话人更名：{args.old} → {args.new}（作用于之后生成的 handoff.md）")
    return overrides


def _cmd_meeting_review_candidate(args) -> dict:
    ws = Workspace(args.ws)
    action = f"{args.review_action}_candidate"  # accept_candidate / reject_candidate
    overrides = meetings.review(ws, args.meeting_id, action, id=args.cand_id)
    zh = "接受" if args.review_action == "accept" else "否决"
    _say(args, f"已{zh}候选结论 {args.cand_id}")
    return overrides


def _cmd_meeting_restore(args) -> dict:
    ws = Workspace(args.ws)
    overrides = meetings.restore(ws, args.meeting_id, args.seq)
    _say(args, f"已按 history.jsonl 前 {args.seq} 条重建 review/overrides.json")
    return overrides


# ---------- stage（stages 模块并行开发中，import 保护） ----------

def _cmd_stage_list(args) -> dict:
    stages = _require_stages()
    if args.pipeline:
        rows = stages.show_pipeline(args.pipeline)["stages"]
        _say(args, "\n".join(
            r if isinstance(r, str) else f"{r.get('id', '?')}\t{r.get('title', '')}"
            for r in rows) or "（无阶段）")
        return {"pipeline": args.pipeline, "stages": rows}
    rows = stages.list_pipelines()
    _say(args, "\n".join(
        r if isinstance(r, str) else f"{r.get('id', '?')}\t{r.get('title', '')}"
        for r in rows) or "（无管道）")
    return {"pipelines": rows}


def _cmd_stage_run(args) -> dict:
    stages = _require_stages()
    ws = Workspace(args.ws)
    llm_fn = None
    if args.use_llm:
        llm = _require_llm()
        cfg = llm.resolve_config()
        if cfg is None:
            raise WritersRoomError(
                "未配置 LLM，无法 --use-llm",
                "设置 WRITERSROOM_LLM_BASE_URL / WRITERSROOM_LLM_API_KEY / "
                "WRITERSROOM_LLM_MODEL，或放弃 --use-llm",
            )
        llm_fn = lambda system, user: llm.chat(cfg, system, user)  # noqa: E731
    ret = stages.run_stage(ws, args.pipeline, args.stage,
                           use_llm=args.use_llm, extra=args.extra or "", llm_fn=llm_fn)
    run_dir = ret if isinstance(ret, Path) else Path(str(ret.get("run_dir", ret)))
    out = run_dir / "output.md"
    _say(args, f"阶段运行完成：{ws.rel(run_dir)}\n产物：{ws.rel(out)}")
    return {"run_dir": ws.rel(run_dir), "output": ws.rel(out)}


def _cmd_stage_confirm(args) -> dict:
    stages = _require_stages()
    ws = Workspace(args.ws)
    stages.confirm(ws, args.pipeline, args.stage, args.run_id)
    current = ws.path("stages", args.pipeline, args.stage, "current.md")
    _say(args, f"已确认 run {args.run_id} → {ws.rel(current)}")
    return {"confirmed": args.run_id, "current": ws.rel(current)}


def _cmd_stage_lock(args) -> dict:
    ws = Workspace(args.ws)
    proj = project.set_lock(ws, args.key)
    _say(args, f"已锁定：{args.key}（共 {len(proj.get('locks', {}))} 项锁定）")
    return {"locks": proj.get("locks", {})}


def _cmd_stage_unlock(args) -> dict:
    ws = Workspace(args.ws)
    proj = project.unset_lock(ws, args.key)
    _say(args, f"已解锁：{args.key}")
    return {"locks": proj.get("locks", {})}


def _cmd_stage_history(args) -> dict:
    stages = _require_stages()
    ws = Workspace(args.ws)
    rows = stages.history(ws, args.pipeline, args.stage)
    if rows:
        _say(args, "\n".join(
            f"{r.get('action', '?')}\t{r.get('run_id', '-')}\t{r.get('created_at', '')}"
            if isinstance(r, dict) else str(r) for r in rows))
    else:
        _say(args, "（无历史记录）")
    return {"history": rows}


# ---------- import / kb / scenes / docgen / llm / audit ----------

def _cmd_import_scan(args) -> dict:
    ws = Workspace(args.ws)
    plan = importer.scan(ws, args.dir)
    s = plan["summary"]
    _say(args, f"扫描完成：{s['total']} 个文件（复制 {s['copy']} / 跳过 {s['skip_exists']} / "
               f"冲突 {s['conflict']}）\n计划：imports/{plan['run_id']}/plan.json")
    return plan


def _cmd_import_apply(args) -> dict:
    ws = Workspace(args.ws)
    report = importer.apply(ws, args.dir)
    s = report["summary"]
    _say(args, f"导入完成：复制 {s['copied']} / 跳过 {s['skip_exists']} / 冲突 {s['conflict']}"
               f"\n报告：imports/{report['run_id']}/report.md")
    return report


def _cmd_kb_search(args) -> dict:
    target = Path(args.dir) if args.dir else Workspace(args.ws)
    result = kb.search(target, args.terms)
    hits = result["hits"]
    if not hits:
        _say(args, f"未命中（已索引 {result['indexed_files']} 个文件）")
        return result
    lines = [f"命中 {len(hits)} 个文件（已索引 {result['indexed_files']} 个）："]
    for h in hits:
        lines.append(f"- {h['file']}（{h['score']}）命中词：{'、'.join(h['matched_terms'])}")
        for ctx in h.get("context", []):
            excerpt = " ".join(ctx["excerpt"].split())
            lines.append(f"    L{ctx['line']}：{excerpt}")
    _say(args, "\n".join(lines))
    return result


def _cmd_scenes_index(args) -> dict:
    ws = Workspace(args.ws)
    run_dir = scenes.scene_index_run(ws, args.source_id)
    doc = read_json(run_dir / "scenes.json")
    lines = [f"场次索引完成：{doc['scene_count']} 场 → {ws.rel(run_dir / 'scenes.md')}"]
    if doc.get("message"):
        lines.append(f"说明：{doc['message']}")
    _say(args, "\n".join(lines))
    return {"run_dir": ws.rel(run_dir), "scene_count": doc["scene_count"],
            "scenes_md": ws.rel(run_dir / "scenes.md")}


def _cmd_docgen(args) -> dict:
    spec_path = Path(args.spec)
    if not spec_path.exists():
        raise WritersRoomError(f"spec 文件不存在：{spec_path}", "检查路径；相对路径以当前目录为基准")
    try:
        spec = read_json(spec_path)
    except json.JSONDecodeError as e:
        raise WritersRoomError(f"spec 不是合法 JSON：{spec_path.name}（{e}）",
                               "修正 JSON 语法后重试") from e
    result = docgen.generate(spec, args.out)
    report = docgen.validate(args.out, spec)
    lines = [
        f"已生成：{args.out}（节 {result['counts']['sections']}，"
        f"段落 {result['counts']['paragraphs']}，表格 {result['counts']['tables']}）",
        "校验报告：",
    ]
    lines += [f"  {'通过' if c['ok'] else '失败'} {c['name']}：{c['detail']}"
              for c in report["checks"]]
    lines.append("校验结论：" + ("全部通过" if report["ok"] else "存在失败项"))
    _say(args, "\n".join(lines))
    if not report["ok"]:
        raise WritersRoomError("DOCX 生成后校验未通过",
                               "查看上方校验报告定位失败项；检查 spec 与 python-docx 环境")
    return {"generate": result, "validate": report}


def _cmd_llm_check(args) -> dict:
    llm = _require_llm()
    cfg = llm.resolve_config(base_url=args.base_url, api_key=args.api_key, model=args.model)
    if cfg is None:
        _say(args, "未启用 LLM：未检测到配置（WRITERSROOM_LLM_BASE_URL 等）；"
                   "一切确定性功能不受影响")
        return {"enabled": False}
    info = llm.check(cfg)
    endpoint = cfg.get("base_url") if isinstance(cfg, dict) else str(cfg)
    model = cfg.get("model", "") if isinstance(cfg, dict) else ""
    _say(args, f"LLM 端点可用：{endpoint}" + (f"（模型 {model}）" if model else ""))
    return {"enabled": True, "endpoint": endpoint, "model": model,
            "check": info if isinstance(info, dict) else {}}


def _cmd_audit(args) -> dict | None:
    repo_root = Path(__file__).resolve().parents[2]
    script = repo_root / "scripts" / "audit.py"
    if not script.exists():
        _say(args, f"未找到 scripts/audit.py（{repo_root}），跳过审计；"
                   "审计脚本由并行任务提供，集成后再运行 writersroom audit")
        return {"skipped": True, "reason": "scripts/audit.py 不存在"}
    spec = importlib.util.spec_from_file_location("writersroom_audit", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.audit(repo_root)
    if isinstance(result, int):
        ok = result == 0
        _say(args, "审计" + ("通过" if ok else "未通过") + f"：{repo_root}")
        if not ok:
            raise WritersRoomError("审计未通过", "查看上方输出定位问题；修复后重跑 writersroom audit")
        return {"ok": True}
    ok = bool(result.get("ok", True))
    checks = result.get("checks") if isinstance(result, dict) else None
    lines = [f"审计{'通过' if ok else '未通过'}：{repo_root}"]
    if isinstance(checks, list):
        for c in checks:
            if isinstance(c, dict) and not c.get("ok", True):
                lines.append(f"  失败 {c.get('name', '?')}：{c.get('detail', '')}")
    _say(args, "\n".join(lines))
    if not ok:
        raise WritersRoomError("审计未通过", "查看上方输出定位问题；修复后重跑 writersroom audit")
    return result if isinstance(result, dict) else {"ok": ok}


# ---------- 解析器 ----------

_JSON_PARENT = argparse.ArgumentParser(add_help=False)
_JSON_PARENT.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                          help="把主要结果以 JSON 打印到 stdout（便于脚本化）")


def _sub(subparsers, name: str, **kw) -> argparse.ArgumentParser:
    return subparsers.add_parser(name, parents=[_JSON_PARENT], **kw)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="writersroom",
        description="本地优先的影视创作研发工具（默认完全离线、零遥测）",
    )
    parser.add_argument("--json", action="store_true", default=False,
                        help="把主要结果以 JSON 打印到 stdout（便于脚本化）")
    parser.add_argument("--version", action="store_true", default=argparse.SUPPRESS,
                        help="显示版本号并退出")
    top = parser.add_subparsers(dest="cmd", required=True, metavar="<命令>")

    p = _sub(top, "init", help="创建工作区")
    p.add_argument("dir", help="工作区目录")
    p.add_argument("--title", default="", help="项目标题")
    p.set_defaults(func=_cmd_init)

    p = _sub(top, "ingest", help="导入剧本/文档（txt/md/fountain/docx/pdf）")
    p.add_argument("ws", help="工作区目录")
    p.add_argument("file", help="待导入文件")
    p.add_argument("--source-id", default=None, help="来源 id（默认取文件名）")
    p.add_argument("--format", default="auto",
                   choices=["auto", "txt", "md", "fountain", "docx", "pdf"])
    p.set_defaults(func=_cmd_ingest)

    p = _sub(top, "analyze", help="对某版本运行确定性分析")
    p.add_argument("ws", help="工作区目录")
    p.add_argument("version_label", help="版本标签")
    p.add_argument("--tokenizer", default="auto", choices=["auto", "char_ngram", "jieba"])
    p.set_defaults(func=_cmd_analyze)

    p = _sub(top, "report", help="打印某版本最新分析报告路径")
    p.add_argument("ws", help="工作区目录")
    p.add_argument("version_label", help="版本标签")
    p.set_defaults(func=_cmd_report)

    p = _sub(top, "version", help="版本标签管理")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "add", help="登记版本标签")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("source_id", help="来源 id")
    q.add_argument("label", help="版本标签")
    q.add_argument("--note", default="", help="备注")
    q.set_defaults(func=_cmd_version_add)
    q = _sub(sp, "list", help="列出版本标签")
    q.add_argument("ws", help="工作区目录")
    q.set_defaults(func=_cmd_version_list)

    p = _sub(top, "profile", help="更新项目档案（project.json）")
    p.add_argument("ws", help="工作区目录")
    p.add_argument("--title", help="项目标题")
    p.add_argument("--format", help="形态：电影/剧集/短片…")
    p.add_argument("--genre", help="类型，逗号分隔，如：悬疑,家庭")
    p.add_argument("--audience", help="受众")
    p.add_argument("--platform", help="平台")
    p.add_argument("--logline", help="一句话故事梗概")
    p.add_argument("--goals", help="创作目标，逗号分隔")
    p.add_argument("--constraints", help='限制（JSON 对象），如 \'{"episodes": 12}\'')
    p.add_argument("--status", help="状态，如：开发中/复盘")
    p.set_defaults(func=_cmd_profile)

    p = _sub(top, "compare", help="比较两个版本的最新分析")
    p.add_argument("ws", help="工作区目录")
    p.add_argument("a", help="版本标签 a")
    p.add_argument("b", help="版本标签 b")
    p.set_defaults(func=_cmd_compare)

    p = _sub(top, "memory", help="候选记忆")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "add", help="新增候选记忆")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("--kind", required=True,
                   help="类型：preference/decision/fact/note")
    q.add_argument("--text", required=True, help="候选内容")
    q.add_argument("--source", default="", help="出处（默认 manual）")
    q.set_defaults(func=_cmd_memory_add)
    q = _sub(sp, "list", help="列出候选记忆")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("--status", help="按有效状态过滤：候选/已确认/提案/否决/暂存/待核问题")
    q.set_defaults(func=_cmd_memory_list)

    p = _sub(top, "decision", help="人工决定（对候选 confirm/propose/reject/park/question）",
             usage="writersroom decision <ws> <动作> <target_id> [--text 文本]"
                   " ｜ writersroom decision list <ws>")
    p.add_argument("argv", nargs="*", metavar="参数")
    p.add_argument("--text", default="", help="决定说明")
    p.set_defaults(func=_cmd_decision)

    p = _sub(top, "tasks", help="任务建议（只生成建议，绝不自动执行）")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "gen", help="生成任务建议清单")
    q.add_argument("ws", help="工作区目录")
    q.set_defaults(func=_cmd_tasks_gen)
    q = _sub(sp, "list", help="查看任务建议清单")
    q.add_argument("ws", help="工作区目录")
    q.set_defaults(func=_cmd_tasks_list)

    p = _sub(top, "meeting", help="会议证据管线")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "locate", help="定位/创建会议目录")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("--date", required=True, help="YYYY-MM-DD")
    q.add_argument("--type", required=True, help="会议类型，如 plan / 策划会")
    q.set_defaults(func=_cmd_meeting_locate)
    q = _sub(sp, "list", help="列出会议")
    q.add_argument("ws", help="工作区目录")
    q.set_defaults(func=_cmd_meeting_list)
    q = _sub(sp, "ingest", help="导入会议转写/笔记（srt/vtt/txt/md/docx）")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("meeting_id", help="会议 id（<日期>_<类型>）")
    q.add_argument("file", help="待导入文件")
    q.set_defaults(func=_cmd_meeting_ingest)
    q = _sub(sp, "normalize", help="规范化 raw/ 为统一 turns")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("meeting_id", help="会议 id")
    q.set_defaults(func=_cmd_meeting_normalize)
    q = _sub(sp, "compact", help="摘录式压缩，生成 handoff.md")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("meeting_id", help="会议 id")
    q.set_defaults(func=_cmd_meeting_compact)
    q = _sub(sp, "review", help="人工覆盖（说话人更名 / 候选结论接受或否决）")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("meeting_id", help="会议 id")
    rp = q.add_subparsers(dest="review_action", required=True, metavar="<动作>")
    r = _sub(rp, "rename-speaker", help="说话人更名（只影响之后生成的 handoff）")
    r.add_argument("--old", required=True, help="原说话人名")
    r.add_argument("--new", required=True, help="新说话人名")
    r.set_defaults(func=_cmd_meeting_review_rename)
    r = _sub(rp, "accept", help="接受候选结论")
    r.add_argument("cand_id", help="候选 id（见最新 compact run）")
    r.set_defaults(func=_cmd_meeting_review_candidate)
    r = _sub(rp, "reject", help="否决候选结论")
    r.add_argument("cand_id", help="候选 id（见最新 compact run）")
    r.set_defaults(func=_cmd_meeting_review_candidate)
    q = _sub(sp, "restore", help="按 history.jsonl 前 N 条重建 overrides.json")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("meeting_id", help="会议 id")
    q.add_argument("--seq", type=int, required=True, help="重放前 N 条历史（0 = 清空覆盖）")
    q.set_defaults(func=_cmd_meeting_restore)

    p = _sub(top, "stage", help="故事开发/创作协作阶段运行器")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "list", help="列出管道或某管道的阶段")
    q.add_argument("pipeline", nargs="?", default=None, help="管道 id（缺省列出全部管道）")
    q.set_defaults(func=_cmd_stage_list)
    q = _sub(sp, "run", help="运行一个阶段（确定性渲染；--use-llm 走 LLM）")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("pipeline", help="管道 id")
    q.add_argument("stage", help="阶段 id")
    q.add_argument("--use-llm", action="store_true", help="把模板+上下文发 LLM")
    q.add_argument("--extra", default="", help="附加说明文本，并入渲染上下文")
    q.set_defaults(func=_cmd_stage_run)
    q = _sub(sp, "confirm", help="人工确认某 run，复制为 current.md")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("pipeline", help="管道 id")
    q.add_argument("stage", help="阶段 id")
    q.add_argument("run_id", help="待确认的 run id")
    q.set_defaults(func=_cmd_stage_confirm)
    q = _sub(sp, "lock", help=f"锁定写前检查项（{_LOCK_KEYS}）")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("key", help="锁定项")
    q.set_defaults(func=_cmd_stage_lock)
    q = _sub(sp, "unlock", help="解除写前锁定")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("key", help="锁定项")
    q.set_defaults(func=_cmd_stage_unlock)
    q = _sub(sp, "history", help="列出某阶段的 run/confirm 历史")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("pipeline", help="管道 id")
    q.add_argument("stage", help="阶段 id")
    q.set_defaults(func=_cmd_stage_history)

    p = _sub(top, "import", help="混合命名目录分类导入")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "scan", help="生成导入计划（只写 plan.json，不复制）")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("dir", help="待整理目录")
    q.set_defaults(func=_cmd_import_scan)
    q = _sub(sp, "apply", help="执行导入并写 report.md（绝不覆盖既有文件）")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("dir", help="待整理目录")
    q.set_defaults(func=_cmd_import_apply)

    p = _sub(top, "kb", help="知识库检索（BM25）")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "search", help="检索知识库")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("terms", nargs="+", help="检索词（可多个）")
    q.add_argument("--dir", default=None, help="覆盖知识库目录（默认 <ws>/knowledge）")
    q.set_defaults(func=_cmd_kb_search)

    p = _sub(top, "scenes", help="场次索引（只摘录，不改写）")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "index", help="生成场次索引")
    q.add_argument("ws", help="工作区目录")
    q.add_argument("source_id", help="来源 id")
    q.set_defaults(func=_cmd_scenes_index)

    p = _sub(top, "docgen", help="按 spec 生成 DOCX 并重开校验")
    p.add_argument("spec", help="spec.json 路径")
    p.add_argument("out", help="输出 .docx 路径")
    p.set_defaults(func=_cmd_docgen)

    p = _sub(top, "llm", help="可选 LLM 适配（默认未启用）")
    sp = p.add_subparsers(dest="sub", required=True, metavar="<子命令>")
    q = _sub(sp, "check", help="LLM 端点连通性检查")
    q.add_argument("--base-url", default=None, help="覆盖端点（否则读环境变量）")
    q.add_argument("--api-key", default=None, help="覆盖 API key")
    q.add_argument("--model", default=None, help="覆盖模型名")
    q.set_defaults(func=_cmd_llm_check)

    p = _sub(top, "audit", help="运行 scripts/audit.py 仓库审计")
    p.set_defaults(func=_cmd_audit)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。返回退出码：0 成功；2 WritersRoomError；1 内部错误。"""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "version", False):
        from . import __version__
        print(f"writersroom {__version__}")
        return 0
    try:
        result = args.func(args)
    except WritersRoomError as e:
        print(e.render(), file=sys.stderr)
        return 2
    except Exception as e:  # 意外异常：exit 1，不伪装成用户错误
        print(f"错误：内部错误：{type(e).__name__}：{e}", file=sys.stderr)
        print("解决：这属于程序缺陷；请保存工作区与完整命令行，反馈给维护者", file=sys.stderr)
        return 1
    if _want_json(args) and result is not None:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
