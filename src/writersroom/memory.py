"""候选记忆 / 人工决定 / 任务生成（docs/design/spec.md §4.5）。

数据流（全部 append-only，人工决定不可变）：
- memory/candidates.jsonl：候选记忆，行 = {id, kind, text, source, status: "candidate", created_at}
- memory/decisions.jsonl：人工决定，行 = {id, target_id, action, text, actor, created_at}
- tasks/tasks.json + tasks/tasks.md：由 gen_tasks 全量重生成的建议清单

id 规则：候选 CRxxxx、决定 DCxxxx（各自按现有文件递增）；任务 Txxxx（每次生成时按输出顺序重排）。

状态的定义：候选的"有效状态"由指向它的最新一条决定推导
（confirm→已确认 / propose→提案 / reject→否决 / park→暂存 / question→待核问题），
没有任何决定时为「候选」。candidates.jsonl 行内的 status 字段恒为 "candidate"（原始记录），
派生状态只存在于查询结果（effective_status / list_memory 的 effective_status 字段）中。

gen_tasks 生成的是**建议清单**：每次运行全量覆盖 tasks.json/tasks.md，不追踪执行状态
（open/closed 不在本模块维护，完成后由人工自行归档）；本模块绝不自动执行任何任务。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, append_jsonl, now_iso, read_json,
    read_jsonl, sha256_file, sha256_text, write_json,
)

CANDIDATE_KINDS = ("preference", "decision", "fact", "note")
DECISION_ACTIONS = ("confirm", "propose", "reject", "park", "question")

# 决定动作 → 有效状态（spec §4.5）
ACTION_STATUS = {
    "confirm": "已确认",
    "propose": "提案",
    "reject": "否决",
    "park": "暂存",
    "question": "待核问题",
}
STATUS_CANDIDATE = "候选"  # 无任何决定时的有效状态
ALL_STATUSES = (STATUS_CANDIDATE, "已确认", "提案", "否决", "暂存", "待核问题")

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"  # core.now_iso() 的输出格式


# ---------- 内部工具 ----------

def _candidates_path(ws: Workspace) -> Path:
    return ws.path("memory", "candidates.jsonl")


def _decisions_path(ws: Workspace) -> Path:
    return ws.path("memory", "decisions.jsonl")


def _next_id(rows: list[dict], prefix: str) -> str:
    """按现有行递增 id（CR0001… / DC0001…）；超过 9999 时自然扩展位数。"""
    n = 0
    for row in rows:
        rid = str(row.get("id", ""))
        if rid.startswith(prefix) and rid[len(prefix):].isdigit():
            n = max(n, int(rid[len(prefix):]))
    return f"{prefix}{n + 1:04d}"


def _latest_decisions(decisions: list[dict]) -> dict[str, dict]:
    """target_id → 最新一条决定（append-only，后写覆盖先写）。"""
    latest: dict[str, dict] = {}
    for d in decisions:
        tid = d.get("target_id")
        if tid:
            latest[str(tid)] = d
    return latest


def _status_map(candidates: list[dict], decisions: list[dict]) -> dict[str, str]:
    latest = _latest_decisions(decisions)
    out: dict[str, str] = {}
    for c in candidates:
        d = latest.get(str(c.get("id", "")))
        out[str(c.get("id", ""))] = ACTION_STATUS.get(d["action"], STATUS_CANDIDATE) if d else STATUS_CANDIDATE
    return out


def _parse_ts(value: str) -> datetime | None:
    """解析 ISO 时间戳；无法解析时返回 None（不抛错，交由调用方跳过）。"""
    if not value:
        return None
    try:
        return datetime.strptime(value, _TS_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------- 候选与决定 ----------

def add_candidate(ws: Workspace, kind: str, text: str, source: str = "") -> dict:
    """新增候选记忆：id 递增 CRxxxx，追加写入 memory/candidates.jsonl，返回该行。

    kind ∈ preference|decision|fact|note；source 记录出处（如 "meeting 2026-08-26_策划"），
    留空时记为 "manual"（人工直接录入）。
    """
    ws.require()
    if kind not in CANDIDATE_KINDS:
        raise WritersRoomError(
            f"未知候选类型：{kind}",
            f"kind 只能是：{'/'.join(CANDIDATE_KINDS)}",
        )
    text = text.strip()
    if not text:
        raise WritersRoomError(
            "候选文本为空",
            "提供候选内容，例如：writersroom memory add note '主角动机需要再确认'",
        )
    rows = read_jsonl(_candidates_path(ws))
    row = {
        "id": _next_id(rows, "CR"),
        "kind": kind,
        "text": text,
        "source": source.strip() or "manual",
        "status": "candidate",
        "created_at": now_iso(),
    }
    append_jsonl(_candidates_path(ws), row)
    return row


def decide(ws: Workspace, target_id: str, action: str, text: str = "", actor: str = "human") -> dict:
    """对某候选追加一条人工决定：id 递增 DCxxxx，写入 memory/decisions.jsonl，返回该行。

    action ∈ confirm|propose|reject|park|question；target_id 必须是已存在的候选 id。
    决定只追加不修改：改变立场就再写一条新决定，有效状态永远取最新一条。
    """
    ws.require()
    if action not in DECISION_ACTIONS:
        raise WritersRoomError(
            f"未知决定动作：{action}",
            f"action 只能是：{'/'.join(DECISION_ACTIONS)}",
        )
    candidates = read_jsonl(_candidates_path(ws))
    known = {str(c.get("id", "")) for c in candidates}
    if target_id not in known:
        raise WritersRoomError(
            f"候选不存在：{target_id}",
            "用 writersroom memory list 查看现有候选 id；或先用 memory add 创建候选",
        )
    rows = read_jsonl(_decisions_path(ws))
    row = {
        "id": _next_id(rows, "DC"),
        "target_id": target_id,
        "action": action,
        "text": text.strip(),
        "actor": actor.strip() or "human",
        "created_at": now_iso(),
    }
    append_jsonl(_decisions_path(ws), row)
    return row


def effective_status(ws: Workspace) -> dict[str, str]:
    """候选 id → 有效状态（最新决定推导；无决定 → 候选）。"""
    ws.require()
    return _status_map(read_jsonl(_candidates_path(ws)), read_jsonl(_decisions_path(ws)))


def list_memory(ws: Workspace, status: str | None = None) -> list[dict]:
    """候选 join 最新决定，按 candidates.jsonl 写入顺序返回。

    每行 = 候选原始字段 + effective_status（派生状态）+ latest_decision（最新决定行或 None）。
    status 参数按有效状态过滤，取值：候选/已确认/提案/否决/暂存/待核问题。
    """
    ws.require()
    if status is not None and status not in ALL_STATUSES:
        raise WritersRoomError(
            f"未知状态过滤值：{status}",
            f"status 只能是：{'/'.join(ALL_STATUSES)}",
        )
    candidates = read_jsonl(_candidates_path(ws))
    latest = _latest_decisions(read_jsonl(_decisions_path(ws)))
    out: list[dict] = []
    for c in candidates:
        cid = str(c.get("id", ""))
        d = latest.get(cid)
        eff = ACTION_STATUS.get(d["action"], STATUS_CANDIDATE) if d else STATUS_CANDIDATE
        if status is not None and eff != status:
            continue
        item = dict(c)
        item["effective_status"] = eff
        item["latest_decision"] = d
        out.append(item)
    return out


# ---------- 任务生成 ----------

def _latest_hints_run(ws: Workspace) -> tuple[str, str, Path, dict] | None:
    """找最新 analysis run 的 hints.json（按 run_id 字典序，时间戳格式即时间序）。

    返回 (version_label, run_id, hints.json 路径, 解析后的 dict)；
    没有任何 run → None；hints.json 损坏 → WritersRoomError。
    """
    analysis = ws.path("analysis")
    best: tuple[tuple[str, str], Path] | None = None
    if analysis.is_dir():
        for p in sorted(analysis.glob("*/*/hints.json")):
            key = (p.parent.name, p.parent.parent.name)  # (run_id, version_label)
            if best is None or key > best[0]:
                best = (key, p)
    if best is None:
        return None
    (run_id, version_label), hints_path = best
    try:
        hints = read_json(hints_path)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise WritersRoomError(
            f"最新分析 run 的 hints.json 无法解析：{ws.rel(hints_path)}（{e}）",
            "检查该文件是否被手动改坏；必要时删除该 run 目录后重跑 writersroom analyze",
        ) from e
    if not isinstance(hints, dict):
        raise WritersRoomError(
            f"hints.json 顶层不是对象：{ws.rel(hints_path)}",
            "该文件应由 writersroom analyze 生成；如被手动改动请恢复或重跑 analyze",
        )
    return version_label, run_id, hints_path, hints


def _iter_dimensions(hints: dict):
    """从 hints.json 取出 (维度 id, 维度条目) 迭代器。

    兼容两种布局（spec §4.3 只固定顶层 disclaimer）：dimensions/hints 为
    {维度 id: 条目} 映射，或为 [{dimension|id: …, …}] 列表。条目需为 dict。
    """
    dims = hints.get("dimensions", hints.get("hints", {}))
    if isinstance(dims, dict):
        for dim_id, entry in dims.items():
            if isinstance(entry, dict):
                yield str(dim_id), entry
    elif isinstance(dims, list):
        for entry in dims:
            if isinstance(entry, dict):
                yield str(entry.get("dimension") or entry.get("id") or "unknown"), entry


def _dedup(items: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    """按 title 去重（保留首次出现），保证重跑输出稳定。"""
    seen: set[str] = set()
    out: list[tuple[str, str, str]] = []
    for item in items:
        if item[0] in seen:
            continue
        seen.add(item[0])
        out.append(item)
    return out


def _render_tasks_md(doc: dict, sections: list[tuple[str, list[dict]]]) -> str:
    lines = [
        "# 任务建议清单（writersroom 自动生成）",
        "",
        f"- 生成时间：{doc['generated_at']}",
        f"- 参数：parked_days={doc['parameters']['parked_days']}",
        "- 说明：本清单仅为建议，**不会自动执行任何任务**；每次生成全量覆盖 tasks.json 与本文件，"
        "不追踪执行状态，完成后请人工自行归档。",
    ]
    if doc.get("message"):
        lines.append(f"- 来源说明：{doc['message']}")
    lines.append("")
    for heading, tasks in sections:
        lines.append(f"## {heading}（{len(tasks)}）")
        lines.append("")
        if not tasks:
            lines.append("（无）")
        for t in tasks:
            lines.append(f"- [ ] {t['id']} {t['title']}")
            lines.append(f"  - 原因：{t['why']}")
            lines.append(f"  - 来源：{t['source_ref']}")
        lines.append("")
    return "\n".join(lines)


def gen_tasks(ws: Workspace, parked_days: int = 14) -> dict:
    """汇总任务建议 → tasks/tasks.json + tasks/tasks.md，返回 tasks.json 对应的 dict。

    三个来源：
    1. 有效状态为「待核问题」的候选；
    2. 最新 analysis run（analysis/<版本>/<run_id>/hints.json）中 priority=high 维度的 risks，
       没有任何分析 run 时跳过该来源并在返回 dict 的 message 字段明示；
    3. 有效状态为「暂存」且暂存时间超过 parked_days 天的候选（以最新 park 决定的 created_at 计）。

    幂等：每次运行全量重生成并覆盖 tasks.json/tasks.md，title 去重；任务状态恒为 open。
    这是**建议清单**，不追踪执行，本模块绝不自动执行任何任务。
    """
    ws.require()
    if parked_days < 0:
        raise WritersRoomError("parked_days 不能为负数", "使用 0 表示所有暂存项都算超期，默认 14")

    candidates = read_jsonl(_candidates_path(ws))
    decisions = read_jsonl(_decisions_path(ws))
    latest = _latest_decisions(decisions)
    statuses = _status_map(candidates, decisions)

    # ① 待核问题
    q_items: list[tuple[str, str, str]] = []
    for c in candidates:
        cid = str(c.get("id", ""))
        if statuses.get(cid) == "待核问题":
            q_items.append((
                f"[待核] {c.get('text', '')}",
                "该候选被标记为待核问题，需人工核实后再 confirm 或 reject",
                f"memory/candidates.jsonl#{cid}",
            ))
    q_items = _dedup(q_items)

    # ② 最新 analysis run 的高优先级风险
    r_items: list[tuple[str, str, str]] = []
    messages: list[str] = []
    hints_info = _latest_hints_run(ws)
    hints_path: Path | None = None
    if hints_info is None:
        messages.append(
            "未找到 analysis/<版本>/<run_id>/hints.json（尚无分析 run），"
            "已跳过「高优先级风险」来源；可先运行 writersroom analyze 生成分析"
        )
    else:
        version_label, run_id, hints_path, hints = hints_info
        ref_prefix = f"analysis/{version_label}/{run_id}/hints.json"
        for dim_id, entry in sorted(_iter_dimensions(hints)):
            if entry.get("priority") != "high":
                continue
            risks = entry.get("risks")
            if not isinstance(risks, list):
                continue
            for risk in risks:
                r_items.append((
                    f"[高风险] {dim_id}：{risk}",
                    f"最新分析 run {version_label}/{run_id} 中维度 {dim_id} 的 priority=high，需评估是否处理",
                    f"{ref_prefix}#{dim_id}",
                ))
        r_items = _dedup(r_items)

    # ③ 暂存超期
    p_items: list[tuple[str, str, str]] = []
    now = datetime.now(timezone.utc)
    for c in candidates:
        cid = str(c.get("id", ""))
        d = latest.get(cid)
        if not d or d.get("action") != "park":
            continue
        parked_at = _parse_ts(str(d.get("created_at", "")))
        if parked_at is None:
            continue
        age = now - parked_at
        if age > timedelta(days=parked_days):
            p_items.append((
                f"[暂存超期] {c.get('text', '')}",
                f"该候选已暂存 {age.days} 天（阈值 {parked_days} 天，暂存于 {d.get('created_at', '')}），"
                "需复盘：confirm、reject 或继续 park",
                f"memory/candidates.jsonl#{cid}",
            ))
    p_items = _dedup(p_items)

    # 汇总：固定顺序（待核问题 → 高风险 → 暂存超期），任务 id 按输出顺序重排
    sections_raw = [("待核问题", q_items), ("高优先级风险", r_items), ("暂存超期", p_items)]
    tasks: list[dict] = []
    sections: list[tuple[str, list[dict]]] = []
    for heading, items in sections_raw:
        group: list[dict] = []
        for title, why, source_ref in items:
            t = {
                "id": f"T{len(tasks) + 1:04d}",
                "title": title,
                "why": why,
                "source_ref": source_ref,
                "status": "open",
            }
            tasks.append(t)
            group.append(t)
        sections.append((heading, group))

    # source_hash：三个输入来源各自的 sha256 组合（无文件记 "-"），可复算
    parts = []
    for p in (_candidates_path(ws), _decisions_path(ws), hints_path):
        parts.append(sha256_file(p) if p is not None and p.exists() else "-")
    source_hash = sha256_text("\n".join(parts))

    doc = {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "generated_at": now_iso(),
        "source_hash": source_hash,
        "parameters": {"parked_days": parked_days},
        "message": "；".join(messages),
        "tasks": tasks,
    }
    write_json(ws.path("tasks", "tasks.json"), doc)
    ws.path("tasks", "tasks.md").write_text(_render_tasks_md(doc, sections), encoding="utf-8")
    return doc
