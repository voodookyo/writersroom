"""版本比较：统计/角色/关键词/八维评分/问题清单（docs/design/spec.md §4.4）。

只消费 analysis/<label>/<run_id>/{stats,hints}.json，与 analyze 模块解耦：
某版本缺 analysis run 时惰性调用 analyze.run_analysis 补齐，再取最新 run。
产物写入 analysis/compare/<a>__vs__<b>/<run_id>/{compare.json, compare.md}。

issues 五分类语义（对 spec §4.4 的固定解释）：
- issue 标识 = 维度 id + 风险文本 sha256 前 12 位；
- new/removed：仅出现于 b/a 的 issue；
- 两版共有的 issue 按所属维度分数升降归类：升 → improved，降 → regressed，不变 → persistent。

stats.json 允许信封嵌套（{"stats": {…}}）或扁平两种形态；
hints.json 的八维容器兼容两种形态：dimensions 为 list[{"id": …}] 或 {id: {…}}；
keywords 兼容 list[str]、list[{"term": …}]、{"items"/"terms": [...]} 或 {term: score}。
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, new_run_id, now_iso,
    read_json, sha256_text, write_json,
)
from .project import validate_label

# 八维（spec §4.3），固定顺序
DIMENSIONS = (
    ("story_core", "故事核心"),
    ("genre_fit", "类型完成度"),
    ("character_drive", "人物驱动力"),
    ("relationship_tension", "关系张力"),
    ("pace_hooks", "节奏/钩子"),
    ("emotional_value", "情绪价值"),
    ("market_identity", "市场辨识度"),
    ("production_feasibility", "制作可行性"),
)
DIMENSION_IDS = tuple(d for d, _ in DIMENSIONS)
DIMENSION_NAMES = dict(DIMENSIONS)

# 参与 stat_diff 的数值统计项（spec §4.2）
STAT_KEYS = (
    "chars_total", "chars_no_space", "paragraphs", "sentences", "scenes",
    "episodes", "dialogue_lines", "dialogue_chars", "est_pages", "reading_minutes",
)


# ---------- 输入装载 ----------

def _stats_body(stats: dict) -> dict:
    """stats.json 允许信封式嵌套：{"schema":…, "stats": {…}} 或扁平两种形态。"""
    inner = stats.get("stats")
    if isinstance(inner, dict) and any(k in inner for k in STAT_KEYS):
        return inner
    return stats


def _version_entry(ws: Workspace, label: str) -> dict:
    p = ws.path("versions.json")
    if p.exists():
        for v in read_json(p).get("versions", []):
            if v.get("label") == label:
                return v
    raise WritersRoomError(
        f"未知版本标签：{label}",
        "用 writersroom version list 查看现有标签；或用 writersroom version add 登记版本",
    )


def _resolve_run_dir(ws: Workspace, label: str, run) -> Path | None:
    """宽容解析 analyze.latest_run 的返回值：Path / run_id 字符串 / 含 run_dir|run_id 的 dict。"""
    if run is None:
        return None
    if isinstance(run, dict):
        run = run.get("run_dir") or run.get("run_id")
        if not run:
            return None
    p = Path(str(run))
    if p.is_absolute() or p.is_dir():
        # 绝对路径，或 analyze 按相对工作区根返回的相对路径（相对 CWD 可解析）
        return p
    for candidate in (ws.path("analysis", label, str(run)), ws.path(str(run))):
        if candidate.is_dir():
            return candidate
    return ws.path("analysis", label, str(run))


def _load_analysis(ws: Workspace, label: str) -> tuple[dict, dict, str]:
    """取 label 最新 analysis run 的 (stats, hints, run_id)；无 run 时自动触发 analyze.run_analysis。"""
    import importlib

    # 经 sys.modules 惰性解析：保证测试注入的替身生效，且与导入顺序无关
    analyze = importlib.import_module("writersroom.analyze")

    run = _resolve_run_dir(ws, label, analyze.latest_run(ws, label))
    if run is None:
        ret = analyze.run_analysis(ws, label)
        run = _resolve_run_dir(ws, label, analyze.latest_run(ws, label))
        if run is None and ret is not None:
            run = _resolve_run_dir(ws, label, ret)
    if run is None:
        raise WritersRoomError(
            f"版本 {label} 没有可用的分析结果",
            f"先运行 writersroom analyze {label}，确认成功后再 compare",
        )
    stats_p, hints_p = run / "stats.json", run / "hints.json"
    if not stats_p.exists() or not hints_p.exists():
        raise WritersRoomError(
            f"版本 {label} 的分析产物不完整：{ws.rel(run)}",
            f"重新运行 writersroom analyze {label} 生成 stats.json / hints.json",
        )
    return _stats_body(read_json(stats_p)), read_json(hints_p), run.name


# ---------- 结构提取（对 analyze 产物形态宽容） ----------

def _dimension_map(hints: dict) -> dict[str, dict]:
    dims = hints.get("dimensions") or hints.get("hints") or {}
    out: dict[str, dict] = {}
    if isinstance(dims, dict):
        for did, d in dims.items():
            if isinstance(d, dict):
                out[str(did)] = d
    else:
        for d in dims:
            if isinstance(d, dict) and d.get("id"):
                out[str(d["id"])] = d
    return out


def _scores(hints: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for did, d in _dimension_map(hints).items():
        s = d.get("score")
        if isinstance(s, (int, float)) and not isinstance(s, bool):
            out[did] = s
    return out


def _risk_text(risk) -> str:
    if isinstance(risk, dict):
        for k in ("text", "risk", "detail"):
            if risk.get(k):
                return str(risk[k])
        return json.dumps(risk, ensure_ascii=False, sort_keys=True)
    return str(risk)


def _issues_of(hints: dict) -> dict[str, dict]:
    """{issue_id: {"id", "dimension", "risk"}}；issue_id = 维度 id + 风险文本 sha256 前 12 位。"""
    out: dict[str, dict] = {}
    for did, d in _dimension_map(hints).items():
        for risk in d.get("risks") or []:
            text = _risk_text(risk)
            iid = f"{did}:{sha256_text(text)[:12]}"
            out.setdefault(iid, {"id": iid, "dimension": did, "risk": text})
    return out


def _keyword_terms(stats: dict) -> set[str]:
    kw = stats.get("keywords")
    if isinstance(kw, dict):
        items = kw.get("items") or kw.get("terms")
        if items is None:  # 形如 {term: score, …, "method": …}
            return {str(k) for k in kw if k != "method"}
    else:
        items = kw
    terms: set[str] = set()
    for it in items or []:
        if isinstance(it, str):
            terms.add(it)
        elif isinstance(it, dict):
            t = it.get("term") or it.get("keyword") or it.get("word")
            if t:
                terms.add(str(t))
        elif isinstance(it, (list, tuple)) and it:
            terms.add(str(it[0]))
    return terms


# ---------- 差异计算 ----------

def _stat_diff(a: dict, b: dict) -> dict:
    out = {}
    for k in STAT_KEYS:
        va, vb = a.get(k, 0), b.get(k, 0)
        numeric = isinstance(va, (int, float)) and isinstance(vb, (int, float))
        out[k] = {"a": va, "b": vb, "delta": vb - va if numeric else None}
    return out


def _character_changes(a: dict, b: dict) -> dict:
    ca = a.get("dialogue_by_character") or {}
    cb = b.get("dialogue_by_character") or {}
    common = sorted(set(ca) & set(cb))
    return {
        "added": sorted(set(cb) - set(ca)),
        "removed": sorted(set(ca) - set(cb)),
        "frequency_delta": {
            ch: {"a": ca[ch], "b": cb[ch], "delta": cb[ch] - ca[ch]} for ch in common
        },
    }


def _keyword_changes(a: dict, b: dict) -> dict:
    ta, tb = _keyword_terms(a), _keyword_terms(b)
    return {"added": sorted(tb - ta), "removed": sorted(ta - tb)}


def _dimension_diff(a: dict, b: dict) -> list[dict]:
    sa, sb = _scores(a), _scores(b)
    out = []
    for did in DIMENSION_IDS:
        va, vb = sa.get(did), sb.get(did)
        delta = vb - va if (va is not None and vb is not None) else None
        out.append({"id": did, "a": va, "b": vb, "delta": delta})
    return out


def _issue_diff(a: dict, b: dict) -> dict:
    ia, ib = _issues_of(a), _issues_of(b)
    sa, sb = _scores(a), _scores(b)

    def entry(issue: dict) -> dict:
        va = sa.get(issue["dimension"])
        vb = sb.get(issue["dimension"])
        return {
            **issue,
            "score_a": va,
            "score_b": vb,
            "score_delta": vb - va if (va is not None and vb is not None) else None,
        }

    new = [entry(ib[k]) for k in sorted(set(ib) - set(ia))]
    removed = [entry(ia[k]) for k in sorted(set(ia) - set(ib))]
    improved, regressed, persistent = [], [], []
    for k in sorted(set(ia) & set(ib)):
        e = entry(ib[k])
        va, vb = e["score_a"], e["score_b"]
        if va is not None and vb is not None and vb > va:
            improved.append(e)
        elif va is not None and vb is not None and vb < va:
            regressed.append(e)
        else:
            persistent.append(e)
    return {"new": new, "removed": removed, "improved": improved,
            "regressed": regressed, "persistent": persistent}


# ---------- Markdown 渲染（人读：差异表格 + 问题清单） ----------

def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def _fmt_delta(v) -> str:
    if v is None:
        return "—"
    return f"{v:+g}" if isinstance(v, (int, float)) else str(v)


def render_markdown(result: dict) -> str:
    """渲染 compare.md 内容（确定性：不含时间戳）。"""
    a, b = result["a"], result["b"]
    lines = [
        f"# 版本比较：{a['label']} → {b['label']}",
        "",
        f"- a：`{a['label']}`（source `{a.get('source_id')}`，run `{a['run_id']}`）",
        f"- b：`{b['label']}`（source `{b.get('source_id')}`，run `{b['run_id']}`）",
        "",
        "## 统计差异",
        "",
        "| 统计项 | a | b | delta |",
        "| --- | --- | --- | --- |",
    ]
    for k in STAT_KEYS:
        d = result["stat_diff"][k]
        lines.append(f"| {k} | {_fmt(d['a'])} | {_fmt(d['b'])} | {_fmt_delta(d['delta'])} |")
    lines += [
        "",
        "## 八维评分差异",
        "",
        "| 维度 | a | b | delta |",
        "| --- | --- | --- | --- |",
    ]
    for d in result["dimension_diff"]:
        name = DIMENSION_NAMES.get(d["id"], d["id"])
        lines.append(f"| {d['id']}（{name}） | {_fmt(d['a'])} | {_fmt(d['b'])} | {_fmt_delta(d['delta'])} |")

    cc = result["character_changes"]
    lines += [
        "",
        "## 角色变化",
        "",
        f"- 新增：{', '.join(cc['added']) if cc['added'] else '无'}",
        f"- 移除：{', '.join(cc['removed']) if cc['removed'] else '无'}",
    ]
    if cc["frequency_delta"]:
        lines += ["", "| 角色 | a 行数 | b 行数 | delta |", "| --- | --- | --- | --- |"]
        for ch in sorted(cc["frequency_delta"]):
            d = cc["frequency_delta"][ch]
            lines.append(f"| {ch} | {_fmt(d['a'])} | {_fmt(d['b'])} | {_fmt_delta(d['delta'])} |")

    kc = result["keyword_changes"]
    lines += [
        "",
        "## 关键词变化",
        "",
        f"- 新增：{', '.join(kc['added']) if kc['added'] else '无'}",
        f"- 移除：{', '.join(kc['removed']) if kc['removed'] else '无'}",
        "",
        "## 问题清单",
    ]
    sections = [
        ("new", "新增问题"), ("removed", "已移除问题"), ("improved", "改善中的问题"),
        ("regressed", "恶化的问题"), ("persistent", "持续存在的问题"),
    ]
    for key, title in sections:
        items = result["issues"][key]
        lines += ["", f"### {title}（{len(items)}）", ""]
        if not items:
            lines.append("无")
        for it in items:
            name = DIMENSION_NAMES.get(it["dimension"], it["dimension"])
            suffix = ""
            if it.get("score_a") is not None and it.get("score_b") is not None:
                suffix = f"（{_fmt(it['score_a'])} → {_fmt(it['score_b'])}）"
            lines.append(f"- [{name}] {it['risk']}{suffix}")
    lines.append("")
    return "\n".join(lines)


# ---------- 主入口 ----------

def compare(ws: Workspace, label_a: str, label_b: str) -> dict:
    """比较两个版本标签的最新 analysis run，写 compare.json/compare.md 并返回 compare dict。"""
    ws.require()
    if label_a == label_b:
        raise WritersRoomError(
            f"两个版本标签相同：{label_a}",
            "compare 需要两个不同标签；用 writersroom version list 查看现有标签",
        )
    validate_label(label_a)
    validate_label(label_b)
    entry_a = _version_entry(ws, label_a)
    entry_b = _version_entry(ws, label_b)
    stats_a, hints_a, run_a = _load_analysis(ws, label_a)
    stats_b, hints_b, run_b = _load_analysis(ws, label_b)

    result = {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "source_hash": sha256_text(json.dumps(
            {"labels": [label_a, label_b], "stats_a": stats_a, "hints_a": hints_a,
             "stats_b": stats_b, "hints_b": hints_b},
            ensure_ascii=False, sort_keys=True)),
        "a": {"label": label_a, "source_id": entry_a.get("source_id"), "run_id": run_a},
        "b": {"label": label_b, "source_id": entry_b.get("source_id"), "run_id": run_b},
        "stat_diff": _stat_diff(stats_a, stats_b),
        "character_changes": _character_changes(stats_a, stats_b),
        "keyword_changes": _keyword_changes(stats_a, stats_b),
        "dimension_diff": _dimension_diff(hints_a, hints_b),
        "issues": _issue_diff(hints_a, hints_b),
    }
    out_dir = ws.path("analysis", "compare", f"{label_a}__vs__{label_b}")
    existing = {p.name for p in out_dir.iterdir() if p.is_dir()} if out_dir.is_dir() else set()
    run_dir = out_dir / new_run_id(existing)
    run_dir.mkdir(parents=True)
    write_json(run_dir / "compare.json", result)
    (run_dir / "compare.md").write_text(render_markdown(result), encoding="utf-8")
    return result
