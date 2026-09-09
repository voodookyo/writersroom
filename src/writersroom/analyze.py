"""确定性分析（docs/design/spec.md §4.2）：统计、六类信号、分析运行。

- analyze_document：字数/段落/句子/场/集/对白/估算页数/关键词(TF-IDF top N)/阅读时长；
- compute_signals：主动动作、冲突、钩子、情绪、高成本、结构六类启发式信号，
  每项均带 evidence（block index 列表）；
- run_analysis：按版本标签运行分析，产物写入 analysis/<label>/<run_id>/；
- 所有数值阈值集中在 data/lexicons/hint_thresholds.json，代码内不出现魔法数。

情绪信号的否定/程度规则（compute_signals docstring 要求的规则说明）：
1. 正向情绪词命中计入 positive，负向词命中计入 negative，按场聚合，均为加权计数。
2. 命中词前 N 个字符（阈值表 emotion.negation_window_chars）内出现否定词表任一词条
   → 本次命中极性反转（正记为负、负记为正）。
3. 同一窗口内出现程度副词表任一词条 → 本次命中权重 ×emotion.degree_boost；
   否定与程度可同时生效（先反转、后加权）。
4. 词表按长度降序构造单个正则做非重叠匹配，每次命中只计一次。
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from importlib import resources
from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, load_lexicon, load_project_lexicon,
    new_run_id, now_iso, read_json, sha256_file, split_sentences, tokenize,
    write_json,
)

LEXICON_NAMES = (
    "active_verbs.txt", "conflict.txt", "hooks.txt",
    "emotion_positive.txt", "emotion_negative.txt",
    "negation.txt", "degree.txt", "high_cost.txt",
)

# 冲突/情绪/高成本词只在这几类正文块中统计（场景头、角色名、注释不计）
_CONTENT_KINDS = ("action", "dialogue", "paragraph")
# 场尾判定前瞻时跳过的块类型（转场/注释/括注不算场景内容）
_HOOK_SKIP_KINDS = ("transition", "note", "parenthetical")
_PRE_SCENE = "（场前）"


def load_thresholds(ws: Workspace | None = None) -> dict:
    """加载 hint_thresholds.json；项目可用 knowledge/lexicons/hint_thresholds.json 覆盖。"""
    if ws is not None:
        override = ws.path("knowledge", "lexicons", "hint_thresholds.json")
        if override.exists():
            return read_json(override)
    ref = resources.files("writersroom.data.lexicons").joinpath("hint_thresholds.json")
    return json.loads(ref.read_text(encoding="utf-8"))


def _lex(ws: Workspace | None, name: str) -> list[str]:
    return load_project_lexicon(ws, name) if ws is not None else load_lexicon(name)


def _page_chars(ws: Workspace | None, thresholds: dict) -> int:
    if ws is not None:
        c = ws.project().get("constraints", {}).get("page_chars")
        if isinstance(c, int) and not isinstance(c, bool) and c > 0:
            return c
    return thresholds["stats"]["default_page_chars"]


def _no_space_len(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


# ---------- 关键词：TF-IDF ----------

def _keywords(blocks: list[dict], method: str, top_n: int) -> dict:
    """以每个 block 为文档计算 TF-IDF：idf = ln((N+1)/(df+1)) + 1，score = tf × idf。

    排序键 (-score, term)，同分按词条字典序，保证确定性。
    """
    docs = [tokenize(b["text"], method)[0] for b in blocks]
    n_docs = len(docs)
    if n_docs == 0:
        return {"method": method, "items": []}
    df: Counter = Counter()
    for toks in docs:
        df.update(set(toks))
    tf: Counter = Counter(t for toks in docs for t in toks)
    items = []
    for term, f in tf.items():
        idf = math.log((n_docs + 1) / (df[term] + 1)) + 1.0
        items.append({"term": term, "score": round(f * idf, 6), "tf": f})
    items.sort(key=lambda x: (-x["score"], x["term"]))
    return {"method": method, "items": items[:top_n]}


# ---------- stats ----------

def analyze_document(doc: dict, ws: Workspace | None = None, tokenizer: str = "auto") -> dict:
    """对规范化文档模型（spec §3）计算确定性统计，返回 stats dict（spec §4.2）。

    paragraphs = action + paragraph 块数；sentences 用 core.split_sentences；
    est_pages = chars_no_space / page_chars（page_chars 读 ws project.json
    constraints.page_chars，默认取阈值表 stats.default_page_chars）；
    keywords 为 TF-IDF top N（阈值表 stats.keywords_top_n），method 记录实际分词方法。
    """
    thresholds = load_thresholds(ws)
    text = doc.get("text", "")
    blocks = doc.get("blocks", [])
    chars_no_space = _no_space_len(text)
    sentences = split_sentences(text)
    episodes = {b["episode"] for b in blocks
                if b.get("kind") == "heading" and b.get("episode")}

    by_char: Counter = Counter()
    current: str | None = None
    for b in blocks:
        kind = b.get("kind")
        if kind == "character":
            current = b["text"].strip()
        elif kind == "dialogue":
            if current:
                by_char[current] += 1
        elif kind != "parenthetical":
            current = None

    page_chars = _page_chars(ws, thresholds)
    _, method = tokenize(text, tokenizer)
    return {
        "chars_total": len(text),
        "chars_no_space": chars_no_space,
        "paragraphs": sum(1 for b in blocks if b.get("kind") in ("action", "paragraph")),
        "sentences": len(sentences),
        "scenes": sum(1 for b in blocks if b.get("kind") == "scene_heading"),
        "episodes": len(episodes),
        "dialogue_lines": sum(1 for b in blocks if b.get("kind") == "dialogue"),
        "dialogue_chars": len(by_char),
        "est_pages": round(chars_no_space / page_chars, 1),
        "dialogue_by_character": dict(sorted(by_char.items())),
        "keywords": _keywords(blocks, method, thresholds["stats"]["keywords_top_n"]),
        "reading_minutes": round(
            chars_no_space / thresholds["stats"]["reading_chars_per_minute"], 1),
        "method": method,
    }


# ---------- signals ----------

def _scene_keys(blocks: list[dict]) -> dict[int, str]:
    """block index → 场标识（场号，无场号用场景序号；首个场景头之前为「（场前）」）。"""
    keys: dict[int, str] = {}
    current = _PRE_SCENE
    n = 0
    for i, b in enumerate(blocks):
        if b.get("kind") == "scene_heading":
            n += 1
            current = str(b.get("scene_no") or n)
        keys[b.get("index", i)] = current
    return keys


def _count_terms(text: str, terms: list[str]) -> int:
    return sum(text.count(t) for t in terms)


def _signal_active_action(blocks: list[dict], verbs: list[str]) -> dict:
    action = [(i, b) for i, b in enumerate(blocks) if b.get("kind") == "action"]
    hits = [(i, b) for i, b in action
            if any(b["text"].lstrip().startswith(v) for v in verbs)]
    return {
        "hits": len(hits),
        "total_action_blocks": len(action),
        "ratio": round(len(hits) / len(action), 4) if action else 0.0,
        "evidence": [b.get("index", i) for i, b in hits],
    }


def _signal_conflict(blocks: list[dict], terms: list[str],
                     scene_keys: dict[int, str]) -> dict:
    per_scene: dict[str, int] = {}
    total = 0
    evidence: list[int] = []
    for i, b in enumerate(blocks):
        if b.get("kind") not in _CONTENT_KINDS:
            continue
        c = _count_terms(b["text"], terms)
        if c:
            key = scene_keys[b.get("index", i)]
            per_scene[key] = per_scene.get(key, 0) + c
            total += c
            evidence.append(b.get("index", i))
    return {"total": total, "per_scene": per_scene, "evidence": evidence}


def _signal_hooks(blocks: list[dict], terms: list[str],
                  scene_keys: dict[int, str]) -> dict:
    """场尾/集尾钩子：场或集的最后一个内容块（转场/注释/括注不算内容块）。"""
    ends: list[dict] = []
    for i, b in enumerate(blocks):
        if b.get("kind") not in _CONTENT_KINDS:
            continue
        j = i + 1
        while j < len(blocks) and blocks[j].get("kind") in _HOOK_SKIP_KINDS:
            j += 1
        if j >= len(blocks) or blocks[j].get("kind") in ("scene_heading", "heading"):
            ends.append((i, b))
    hits: list[tuple[int, dict]] = []
    per_scene: dict[str, int] = {}
    for i, b in ends:
        c = _count_terms(b["text"], terms)
        if c:
            hits.append((i, b))
            key = scene_keys[b.get("index", i)]
            per_scene[key] = per_scene.get(key, 0) + c
    return {
        "hits": len(hits),
        "checked_ends": len(ends),
        "per_scene": per_scene,
        "evidence": [b.get("index", i) for i, b in hits],
    }


def _signal_emotion(blocks: list[dict], lexicons: dict[str, list[str]],
                    cfg: dict, scene_keys: dict[int, str]) -> dict:
    pos_words = sorted(lexicons["emotion_positive.txt"], key=len, reverse=True)
    neg_words = sorted(lexicons["emotion_negative.txt"], key=len, reverse=True)
    pos_set, neg_set = set(pos_words), set(neg_words)
    words = pos_words + neg_words
    result = {"positive": 0.0, "negative": 0.0, "hits": 0,
              "per_scene": {}, "evidence": []}
    if not words:
        return result
    pattern = re.compile("|".join(re.escape(w) for w in words))
    negation = lexicons["negation.txt"]
    degree = lexicons["degree.txt"]
    window = cfg["negation_window_chars"]
    boost = cfg["degree_boost"]

    pos_total = neg_total = 0.0
    hits = 0
    per_scene: dict[str, dict[str, float]] = {}
    evidence: list[int] = []
    for i, b in enumerate(blocks):
        if b.get("kind") not in _CONTENT_KINDS:
            continue
        text = b["text"]
        block_hit = False
        for m in pattern.finditer(text):
            word = m.group(0)
            polarity = 1.0 if word in pos_set else -1.0
            prefix = text[max(0, m.start() - window):m.start()]
            if any(neg in prefix for neg in negation):
                polarity = -polarity
            weight = boost if any(d in prefix for d in degree) else 1.0
            hits += 1
            block_hit = True
            key = scene_keys[b.get("index", i)]
            slot = per_scene.setdefault(key, {"positive": 0.0, "negative": 0.0})
            if polarity > 0:
                pos_total += weight
                slot["positive"] += weight
            else:
                neg_total += weight
                slot["negative"] += weight
        if block_hit:
            evidence.append(b.get("index", i))
    return {
        "positive": round(pos_total, 2),
        "negative": round(neg_total, 2),
        "hits": hits,
        "per_scene": {k: {"positive": round(v["positive"], 2),
                          "negative": round(v["negative"], 2)}
                      for k, v in per_scene.items()},
        "evidence": evidence,
    }


def _signal_high_cost(blocks: list[dict], terms: list[str],
                      scene_keys: dict[int, str]) -> dict:
    per_scene: dict[str, int] = {}
    matched: set[str] = set()
    total = 0
    evidence: list[int] = []
    for i, b in enumerate(blocks):
        if b.get("kind") not in _CONTENT_KINDS:
            continue
        c = _count_terms(b["text"], terms)
        if c:
            key = scene_keys[b.get("index", i)]
            per_scene[key] = per_scene.get(key, 0) + c
            total += c
            matched.update(t for t in terms if t in b["text"])
            evidence.append(b.get("index", i))
    return {"hits": total, "per_scene": per_scene, "terms": sorted(matched),
            "evidence": evidence}


def _signal_structure(blocks: list[dict], scene_keys: dict[int, str]) -> dict:
    scene_chars: dict[str, int] = {}
    scene_blocks: dict[str, list[int]] = {}
    dialogue_chars = 0
    action_chars = 0
    for i, b in enumerate(blocks):
        idx = b.get("index", i)
        n = _no_space_len(b["text"])
        kind = b.get("kind")
        if kind == "dialogue":
            dialogue_chars += n
        elif kind == "action":
            action_chars += n
        key = scene_keys[idx]
        scene_chars[key] = scene_chars.get(key, 0) + n
        scene_blocks.setdefault(key, []).append(idx)
    longest = max(scene_chars.items(), key=lambda kv: kv[1]) if scene_chars else None
    return {
        "scene_lengths": scene_chars,
        "dialogue_chars_total": dialogue_chars,
        "action_chars_total": action_chars,
        "dialogue_action_ratio": (
            round(dialogue_chars / action_chars, 4) if action_chars else None),
        "longest_scene": ({"scene": longest[0], "chars": longest[1]}
                          if longest else None),
        "evidence": scene_blocks.get(longest[0], []) if longest else [],
    }


def compute_signals(doc: dict, ws: Workspace | None = None) -> dict:
    """六类确定性启发式信号（spec §4.2），每项带 evidence（block index 列表）。

    情绪规则：正向/负向词表命中按场聚合成加权计数；命中词前
    emotion.negation_window_chars 字符内出现否定词 → 极性反转；同窗口内出现
    程度副词 → 权重 ×emotion.degree_boost（先反转后加权，可同时生效）。
    词表长词优先、非重叠匹配。阈值取自 data/lexicons/hint_thresholds.json，
    词表可用 knowledge/lexicons/ 同名文件项目级覆盖。
    """
    thresholds = load_thresholds(ws)
    blocks = doc.get("blocks", [])
    scene_keys = _scene_keys(blocks)
    lexicons = {name: _lex(ws, name) for name in LEXICON_NAMES}
    return {
        "active_action": _signal_active_action(blocks, lexicons["active_verbs.txt"]),
        "conflict": _signal_conflict(blocks, lexicons["conflict.txt"], scene_keys),
        "hooks": _signal_hooks(blocks, lexicons["hooks.txt"], scene_keys),
        "emotion": _signal_emotion(blocks, lexicons, thresholds["emotion"], scene_keys),
        "high_cost": _signal_high_cost(blocks, lexicons["high_cost.txt"], scene_keys),
        "structure": _signal_structure(blocks, scene_keys),
    }


# ---------- 运行 ----------

def latest_run(ws: Workspace, version_label: str) -> Path | None:
    """返回该版本最近一次分析 run 目录；无则 None。"""
    base = ws.path("analysis", version_label)
    if not base.is_dir():
        return None
    runs = sorted(p for p in base.iterdir() if p.is_dir())
    return runs[-1] if runs else None


def run_analysis(ws: Workspace, version_label: str, tokenizer: str = "auto") -> Path:
    """对 versions.json 中 version_label 对应的规范化文档运行完整分析。

    产物写入 analysis/<version_label>/<run_id>/：stats.json、signals.json、
    hints.json、manifest.json、report.md。版本不存在或规范化文档缺失时报
    WritersRoomError（带可操作提示）。
    """
    ws.require()
    versions = read_json(ws.path("versions.json")).get("versions", [])
    match = next((v for v in versions if v.get("label") == version_label), None)
    if match is None:
        known = "、".join(str(v.get("label", "?")) for v in versions) or "（无）"
        raise WritersRoomError(
            f"版本不存在：{version_label}",
            f"已登记版本：{known}；先用 writersroom version add 登记，"
            f"或用 writersroom version list 查看",
        )
    source_id = match.get("source_id")
    norm = ws.path("normalized", f"{source_id}.json")
    if not norm.exists():
        raise WritersRoomError(
            f"规范化文档缺失：{ws.rel(norm)}",
            f"normalized/ 可重建：重新运行 writersroom ingest 导入 "
            f"sources/{source_id}/ 下的原始文件",
        )
    doc = read_json(norm)
    src_hash = sha256_file(norm)

    stats = analyze_document(doc, ws, tokenizer)
    signals = compute_signals(doc, ws)
    from .hints import build_hints  # 延迟导入：hints 依赖本模块的 load_thresholds
    hints = build_hints(doc, stats, signals, ws.project())
    hints["source_hash"] = src_hash

    base = ws.path("analysis", version_label)
    existing = {p.name for p in base.iterdir() if p.is_dir()} if base.is_dir() else set()
    run_id = new_run_id(existing)
    run_dir = base / run_id
    created = now_iso()

    def artifact(key: str, payload: dict) -> dict:
        return {"schema": 1, "produced_by": TOOL, "created_at": created,
                "source_hash": src_hash, key: payload}

    write_json(run_dir / "stats.json", artifact("stats", stats))
    write_json(run_dir / "signals.json", artifact("signals", signals))
    write_json(run_dir / "hints.json", hints)
    write_json(run_dir / "manifest.json", {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": created,
        "run_id": run_id,
        "version_label": version_label,
        "source_id": source_id,
        "inputs": [{"path": ws.rel(norm), "sha256": src_hash}],
        "params": {"tokenizer": tokenizer, "tokenizer_used": stats["method"]},
        "tool": TOOL,
        "outputs": ["stats.json", "signals.json", "hints.json",
                    "manifest.json", "report.md"],
    })
    report = _render_report(version_label, run_id, created, doc, stats, signals, hints)
    (run_dir / "report.md").write_text(report, encoding="utf-8")
    return run_dir


# ---------- report.md（给人看） ----------

def _render_report(version_label: str, run_id: str, created: str, doc: dict,
                   stats: dict, signals: dict, hints: dict) -> str:
    from .hints import DIMENSION_LABELS

    lines: list[str] = []
    a = lines.append
    source = doc.get("source", {})
    a(f"# 分析报告 — {version_label}")
    a("")
    a(f"- run_id：`{run_id}`")
    a(f"- 生成时间：{created}")
    if source.get("original_path"):
        a(f"- 来源：`{source['original_path']}`"
          f"（sha256 `{str(source.get('sha256', ''))[:12]}…`）")
    a(f"- 分词方法：{stats['method']}")
    a("")
    a("## 统计")
    a("")
    a("| 指标 | 值 |")
    a("|---|---|")
    for label, key in (("总字数", "chars_total"), ("去空白字数", "chars_no_space"),
                       ("段落", "paragraphs"), ("句子", "sentences"),
                       ("场", "scenes"), ("集", "episodes"),
                       ("对白行", "dialogue_lines"), ("对白角色数", "dialogue_chars"),
                       ("估算页数", "est_pages"), ("预计阅读分钟", "reading_minutes")):
        a(f"| {label} | {stats[key]} |")
    a("")
    a("## 角色对白分布")
    a("")
    a("| 角色 | 对白行数 |")
    a("|---|---|")
    for name, n in stats["dialogue_by_character"].items():
        a(f"| {name} | {n} |")
    a("")
    kw = stats["keywords"]
    a(f"## 关键词（TF-IDF top{len(kw['items'])}，method={kw['method']}）")
    a("")
    a("、".join(f"{it['term']}（{it['score']}）" for it in kw["items"]) or "（无）")
    a("")
    a("## 信号摘要")
    a("")
    act = signals["active_action"]
    a(f"- 主动动作开头：{act['hits']}/{act['total_action_blocks']}"
      f"（比例 {act['ratio']}）")
    con = signals["conflict"]
    a(f"- 冲突：共 {con['total']} 处；分布 "
      + ("、".join(f"场{k} {n} 处" for k, n in con["per_scene"].items()) or "（无）"))
    hk = signals["hooks"]
    a(f"- 钩子：场尾/集尾命中 {hk['hits']}/{hk['checked_ends']} 处")
    em = signals["emotion"]
    a(f"- 情绪：正向 {em['positive']} / 负向 {em['negative']}（加权，"
      f"共 {em['hits']} 次命中）")
    hc = signals["high_cost"]
    a(f"- 高成本场面：{hc['hits']} 处（{('、'.join(hc['terms'])) or '无'}）")
    st = signals["structure"]
    longest = st["longest_scene"] or {"scene": "-", "chars": 0}
    a(f"- 结构：最长场 场{longest['scene']}（{longest['chars']} 字）；"
      f"对白/动作比 {st['dialogue_action_ratio']}")
    a("")
    a("## 八维开发提示")
    a("")
    a(f"> {hints['disclaimer']}")
    a("")
    a("| 维度 | 评分 | 优先级 |")
    a("|---|---|---|")
    for dim_id, d in hints["dimensions"].items():
        a(f"| {DIMENSION_LABELS.get(dim_id, dim_id)} | {d['score']} | {d['priority']} |")
    a("")
    for dim_id, d in hints["dimensions"].items():
        a(f"### {DIMENSION_LABELS.get(dim_id, dim_id)}（{dim_id}）：{d['score']} 分")
        a("")
        for r in d["reasoning"]:
            a(f"- 依据：{r}")
        for r in d["risks"]:
            a(f"- 风险：{r}")
        for s in d["suggestions"]:
            a(f"- 建议：{s}")
        a("")
    return "\n".join(lines)
