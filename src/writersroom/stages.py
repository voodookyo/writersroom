"""故事开发 / 创作协作阶段运行器（docs/design/spec.md §4.7）。

原则：
- 默认确定性模板渲染（零 LLM、零网络）；产物一律为「候选」，人工 confirm 后才成为 current.md。
- --use-llm 时经调用方注入的 llm_fn(system, user) 生成候选，产物标 layer=llm-candidate；
  llm_fn 抛错则原样上抛且不写 output.md（不伪造成功）。
- 正文类阶段（needs_locks）强制写前锁定八项检查（spec §4.7 末段）。
- 重跑写新 runs/<run_id>/，永不覆盖；history.jsonl append-only。
"""
from __future__ import annotations

import json
import re
import shutil
from importlib import resources
from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, append_jsonl, new_run_id, now_iso,
    read_json, read_jsonl, sha256_text, write_json,
)
from .project import LOCK_KEYS

LOCK_KEYS_LABEL = {
    "premise": "前提", "structure": "结构", "character_choice": "人物主动选择",
    "causality": "因果链", "relationship_stage": "关系阶段", "event_coverage": "事件覆盖",
    "episode_hook": "集尾钩子", "production_boundary": "制作边界",
}

UNSTABLE_WARNING = ("外部网站、登录状态和页面变化为不稳定依赖：本阶段涉及的任何外部信息"
                    "须由人工核实来源与时效，工具不做自动抓取。")

_LLM_SYSTEM = (
    "你是影视剧本研发助手。根据给定模板与上下文产出结构化候选文档。"
    "你的输出永远是候选，最终采用由人类主创决定；不得声称任何结论为最终决定。"
)


# ---------- 管道定义 ----------

def _load_pipeline(pipeline_id: str) -> dict:
    ref = resources.files("writersroom.data.pipelines").joinpath(f"{pipeline_id}.json")
    try:
        text = ref.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise WritersRoomError(
            f"未知管道：{pipeline_id}",
            f"可用管道：{', '.join(p['id'] for p in list_pipelines())}",
        ) from None
    return json.loads(text)


def list_pipelines() -> list[dict]:
    out = []
    for ref in sorted(resources.files("writersroom.data.pipelines").iterdir()):
        if ref.name.endswith(".json"):
            p = json.loads(ref.read_text(encoding="utf-8"))
            out.append({"id": p["id"], "title": p["title"], "stages": len(p["stages"])})
    return out


def show_pipeline(pipeline_id: str) -> dict:
    return _load_pipeline(pipeline_id)


def _stage(pipeline: dict, stage_id: str) -> dict:
    for st in pipeline["stages"]:
        if st["id"] == stage_id:
            return st
    raise WritersRoomError(
        f"管道 {pipeline['id']} 中无阶段 {stage_id}",
        f"可用阶段：{', '.join(s['id'] for s in pipeline['stages'])}",
    )


def _template_text(name: str) -> str:
    ref = resources.files("writersroom.data.templates").joinpath(name)
    try:
        return ref.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise WritersRoomError(f"模板缺失：{name}", "检查包数据 data/templates/ 是否完整") from None


# ---------- 上下文与渲染 ----------

_VAR = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def build_context(ws: Workspace, pipeline_id: str, stage_id: str) -> dict:
    """渲染变量：project.* / today / stage.title / decisions.confirmed / prev.<stage_id>。"""
    ws.require()
    proj = ws.project()
    pipeline = _load_pipeline(pipeline_id)
    stage = _stage(pipeline, stage_id)

    confirmed_lines: list[str] = []
    cands = ws.path("memory", "candidates.jsonl")
    decisions = read_jsonl(ws.path("memory", "decisions.jsonl"))
    latest: dict[str, dict] = {}
    for d in decisions:
        latest[d["target_id"]] = d
    for c in read_jsonl(cands):
        d = latest.get(c["id"])
        if d and d["action"] == "confirm":
            confirmed_lines.append(f"- [{c['id']}] {c['text']}")
    confirmed = "\n".join(confirmed_lines) if confirmed_lines else "（暂无已确认决定）"

    ctx = {
        "project.title": proj.get("title", ""),
        "project.logline": proj.get("logline", ""),
        "project.genre": "、".join(proj.get("genre", [])) or "（未填）",
        "project.format": proj.get("format", "") or "（未填）",
        "project.audience": proj.get("audience", "") or "（未填）",
        "project.platform": proj.get("platform", "") or "（未填）",
        "project.goals": "、".join(proj.get("goals", [])) or "（未填）",
        "today": now_iso()[:10],
        "stage.title": stage["title"],
        "decisions.confirmed": confirmed,
    }
    for st in pipeline["stages"]:
        if st["id"] == stage_id:
            break
        cur = ws.path("stages", pipeline_id, st["id"], "current.md")
        if cur.exists():
            text = cur.read_text(encoding="utf-8")[:500]
            ctx[f"prev.{st['id']}"] = text + ("…" if len(cur.read_text(encoding="utf-8")) > 500 else "")
        else:
            ctx[f"prev.{st['id']}"] = "（前序阶段未确认）"
    return ctx


def render(template: str, ctx: dict) -> tuple[str, list[str]]:
    """替换 {{var}}；未知变量保留原文并记 warning。"""
    warnings: list[str] = []

    def sub(m: re.Match) -> str:
        key = m.group(1)
        if key in ctx:
            return str(ctx[key])
        warnings.append(f"未知模板变量保留原文：{{{{{key}}}}}")
        return m.group(0)

    return _VAR.sub(sub, template), warnings


# ---------- 运行 / 确认 / 历史 ----------

def run_stage(ws: Workspace, pipeline_id: str, stage_id: str, use_llm: bool = False,
              llm_fn=None, extra: str = "") -> Path:
    ws.require()
    pipeline = _load_pipeline(pipeline_id)
    stage = _stage(pipeline, stage_id)

    if stage.get("needs_locks"):
        locks = ws.project().get("locks", {})
        missing = [k for k in LOCK_KEYS if not locks.get(k)]
        if missing:
            names = "、".join(f"{k}（{LOCK_KEYS_LABEL.get(k, k)}）" for k in missing)
            raise WritersRoomError(
                f"写前锁定未完成，缺失：{names}",
                "编剧工作先解决前提/结构/人物选择/因果再进入正文："
                "逐项运行 writersroom stage lock <key> 由人工主创锁定后再执行",
            )

    ctx = build_context(ws, pipeline_id, stage_id)
    ctx["extra"] = extra
    template = _template_text(stage["template"])
    body, warnings = render(template, ctx)
    layer = "deterministic-template"
    if stage.get("unstable_external"):
        warnings.append(UNSTABLE_WARNING)
    if extra:
        body += f"\n\n## 本次运行附加输入\n\n{extra}\n"

    if use_llm:
        if llm_fn is None:
            raise WritersRoomError(
                "未配置 LLM",
                "设置 WRITERSROOM_LLM_BASE_URL（如 http://localhost:11434/v1）与 "
                "WRITERSROOM_LLM_MODEL 后重试，或去掉 --use-llm 使用确定性模板",
            )
        user_prompt = body + "\n\n请按模板结构产出该阶段的候选内容。"
        result = llm_fn(_LLM_SYSTEM, user_prompt)  # 抛错则上抛，不写任何产物
        body = result
        layer = "llm-candidate"

    run_id = new_run_id({p.name for p in ws.path("stages", pipeline_id, stage_id, "runs").glob("*")}
                        if ws.path("stages", pipeline_id, stage_id, "runs").exists() else set())
    run_dir = ws.path("stages", pipeline_id, stage_id, "runs", run_id)
    run_dir.mkdir(parents=True)

    header = (f"> 层级：{layer} ｜ 管道：{pipeline_id} ｜ 阶段：{stage_id} ｜ 运行：{run_id}\n"
              f"> 本文件为候选产物，须人工 confirm 后方生效\n")
    if stage.get("unstable_external"):
        header += f"> ⚠ {UNSTABLE_WARNING}\n"
    (run_dir / "output.md").write_text(header + "\n" + body, encoding="utf-8")

    manifest = {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "run_id": run_id,
        "pipeline": pipeline_id,
        "stage": stage_id,
        "layer": layer,
        "use_llm": use_llm,
        "template": stage["template"],
        "input_hash": sha256_text(template + json.dumps(ctx, ensure_ascii=False, sort_keys=True) + extra),
        "warnings": warnings,
        "unstable_external": bool(stage.get("unstable_external")),
    }
    write_json(run_dir / "manifest.json", manifest)
    append_jsonl(ws.path("stages", pipeline_id, stage_id, "history.jsonl"), {
        "seq": len(read_jsonl(ws.path("stages", pipeline_id, stage_id, "history.jsonl"))) + 1,
        "action": "run", "run_id": run_id, "layer": layer,
        "actor": "tool", "created_at": now_iso(),
    })
    return run_dir


def confirm(ws: Workspace, pipeline_id: str, stage_id: str, run_id: str,
            actor: str = "human") -> Path:
    ws.require()
    pipeline = _load_pipeline(pipeline_id)
    _stage(pipeline, stage_id)
    run_dir = ws.path("stages", pipeline_id, stage_id, "runs", run_id)
    output = run_dir / "output.md"
    if not output.exists():
        existing = sorted(p.name for p in ws.path("stages", pipeline_id, stage_id, "runs").glob("*")) \
            if ws.path("stages", pipeline_id, stage_id, "runs").exists() else []
        raise WritersRoomError(
            f"运行不存在：{run_id}",
            f"可用 run：{', '.join(existing) or '（无，先 stage run）'}",
        )
    current = ws.path("stages", pipeline_id, stage_id, "current.md")
    shutil.copyfile(output, current)
    append_jsonl(ws.path("stages", pipeline_id, stage_id, "history.jsonl"), {
        "seq": len(read_jsonl(ws.path("stages", pipeline_id, stage_id, "history.jsonl"))) + 1,
        "action": "confirm", "run_id": run_id, "actor": actor, "created_at": now_iso(),
    })
    return current


def history(ws: Workspace, pipeline_id: str, stage_id: str) -> list[dict]:
    ws.require()
    pipeline = _load_pipeline(pipeline_id)
    _stage(pipeline, stage_id)
    return read_jsonl(ws.path("stages", pipeline_id, stage_id, "history.jsonl"))
