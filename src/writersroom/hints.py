"""八维开发提示（docs/design/spec.md §4.3）。

打分完全由数据驱动：每维的 base 分与每条规则（指标、比较符、阈值、增减分、
reasoning/risk/suggestion 模板、证据引用）都定义在
data/lexicons/hint_thresholds.json 的 dimensions 段中，本模块只负责求值，
代码内无任何评分魔法数。同输入 + 同阈值表 → 同输出（created_at 除外）。

注意：build_hints 不接收 Workspace，因此始终使用包内阈值表；analyze 层
（stats/signals）才支持 knowledge/lexicons/ 项目级覆盖。
"""
from __future__ import annotations

from .analyze import load_thresholds
from .core import TOOL, WritersRoomError, now_iso

DISCLAIMER = "启发式开发提示，非艺术结论或平台认证"

DIMENSION_LABELS = {
    "story_core": "故事核心",
    "genre_fit": "类型完成度",
    "character_drive": "人物驱动力",
    "relationship_tension": "关系张力",
    "pace_hooks": "节奏/钩子",
    "emotional_value": "情绪价值",
    "market_identity": "市场辨识度",
    "production_feasibility": "制作可行性",
}

_OPS = {
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    "==": lambda a, b: a == b,
}


def _metric_context(stats: dict, signals: dict, project: dict,
                    thresholds: dict) -> dict[str, float]:
    """把 stats/signals/project 折算成规则可引用的数值指标（阈值在规则表内）。"""
    scenes = stats.get("scenes", 0)
    hooks = signals.get("hooks", {})
    emotion = signals.get("emotion", {})
    structure = signals.get("structure", {})
    pos = emotion.get("positive", 0.0)
    neg = emotion.get("negative", 0.0)

    genre_list = project.get("genre") or []
    gmap = thresholds.get("genre_signal_map", {})
    expected: set[str] = set()
    for g in genre_list:
        expected.update(gmap.get(g, gmap.get("_default", [])))
    hit_signals = 0
    for sig in sorted(expected):
        s = signals.get(sig, {})
        if s.get("total", s.get("hits", 0)):
            hit_signals += 1

    longest = structure.get("longest_scene") or {}
    return {
        "scenes": scenes,
        "episodes": stats.get("episodes", 0),
        "dialogue_lines": stats.get("dialogue_lines", 0),
        "dialogue_chars": stats.get("dialogue_chars", 0),
        "est_pages": stats.get("est_pages", 0),
        "keywords_count": len(stats.get("keywords", {}).get("items", [])),
        "active_ratio": signals.get("active_action", {}).get("ratio", 0.0),
        "conflict_total": signals.get("conflict", {}).get("total", 0),
        "conflict_scenes": len(signals.get("conflict", {}).get("per_scene", {})),
        "hooks_hits": hooks.get("hits", 0),
        "hooks_coverage": round(hooks.get("hits", 0) / scenes, 4) if scenes else 0.0,
        "emotion_positive": pos,
        "emotion_negative": neg,
        "emotion_total": round(pos + neg, 4),
        "emotion_range": 1 if (pos > 0 and neg > 0) else 0,
        "high_cost_hits": signals.get("high_cost", {}).get("hits", 0),
        "longest_scene_chars": longest.get("chars", 0),
        "dialogue_action_ratio": structure.get("dialogue_action_ratio") or 0,
        "genre_registered": 1 if genre_list else 0,
        "genre_signal_coverage": (
            round(hit_signals / len(expected), 4) if expected else 0.0),
        "logline_present": 1 if project.get("logline") else 0,
        "audience_present": 1 if project.get("audience") else 0,
    }


def _fmt_num(v) -> str:
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else str(round(v, 2))
    return str(v)


def _dedupe(items: list) -> list:
    seen: set = set()
    out: list = []
    for x in items:
        key = (type(x).__name__, x)
        if key not in seen:
            seen.add(key)
            out.append(x)
    return out


def _eval_dimension(dim_id: str, dim_cfg: dict, metrics: dict,
                    signals: dict, prio_cfg: dict) -> dict:
    score = dim_cfg["base"]
    reasoning: list[str] = []
    risks: list[str] = []
    suggestions: list[str] = []
    evidence: list = []
    for rule in dim_cfg.get("rules", []):
        metric = rule["metric"]
        if metric not in metrics:
            raise WritersRoomError(
                f"hint_thresholds.json 维度 {dim_id} 引用了未知指标：{metric}",
                f"可用指标：{', '.join(sorted(metrics))}；"
                f"修正 data/lexicons/hint_thresholds.json（或项目覆盖）后重试",
            )
        op = _OPS.get(rule["op"])
        if op is None:
            raise WritersRoomError(
                f"hint_thresholds.json 维度 {dim_id} 使用了未知比较符：{rule['op']}",
                f"支持的比较符：{', '.join(sorted(_OPS))}",
            )
        actual = metrics[metric]
        if not op(actual, rule["value"]):
            continue
        score += rule.get("delta", 0)
        ctx = {"actual": _fmt_num(actual), "value": _fmt_num(rule["value"])}
        if rule.get("reasoning"):
            reasoning.append(rule["reasoning"].format(**ctx))
        if rule.get("risk"):
            risks.append(rule["risk"].format(**ctx))
        if rule.get("suggestion"):
            suggestions.append(rule["suggestion"].format(**ctx))
        sig = rule.get("evidence_signal")
        if sig and sig in signals:
            evidence.extend(signals[sig].get("evidence", []))
        for s in rule.get("evidence_stats", []):
            evidence.append(f"stats:{s}")
    score = max(0, min(5, score))  # spec §4.3：score ∈ [0, 5]
    if not reasoning:
        reasoning.append(
            f"相关指标均处于阈值表基线区间（base={dim_cfg['base']}），未触发增减分规则。")
    if score <= prio_cfg["high_max"]:
        priority = "high"
    elif score <= prio_cfg["medium_max"]:
        priority = "medium"
    else:
        priority = "low"
    return {
        "label": DIMENSION_LABELS.get(dim_id, dim_id),
        "score": score,
        "evidence": _dedupe(evidence),
        "reasoning": reasoning,
        "risks": risks,
        "priority": priority,
        "suggestions": suggestions,
    }


def build_hints(doc: dict, stats: dict, signals: dict, project: dict) -> dict:
    """按 spec §4.3 生成八维开发提示。

    每维输出 score（0–5，阈值表 base + 命中规则 delta 之和截断）、
    evidence（block index 与 stats: 引用）、reasoning（≥1 条中文模板）、
    risks、priority（high|medium|low，由 priority 阈值段映射）、suggestions。
    顶层固定 disclaimer。doc 参数保留用于未来扩展（如引用原文摘录），当前
    评分只依赖已计算的 stats/signals/project。
    """
    thresholds = load_thresholds()
    metrics = _metric_context(stats, signals, project, thresholds)
    prio_cfg = thresholds["priority"]
    dimensions = {}
    for dim_id, dim_cfg in thresholds["dimensions"].items():
        dimensions[dim_id] = _eval_dimension(dim_id, dim_cfg, metrics, signals,
                                             prio_cfg)
    return {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "disclaimer": DISCLAIMER,
        "dimensions": dimensions,
    }
