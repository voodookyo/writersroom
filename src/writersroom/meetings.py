"""会议证据管线：meetings/<meeting_id>/ 分层（raw → normalized → runs → review）。

设计要点（docs/design/spec.md §4.6）：
- 分层不可变：raw/ 原始证据复制后不覆盖；normalized.json 由 normalize 重建，review 永不触碰；
  自动结果写入 runs/<run_id>/（重跑不覆盖）；人工覆盖只写 review/。
- 未知说话人一律保留 "UNKNOWN" 或源文件原标签（如「说话人1」），严禁猜测命名；
  confidence 仅当源格式自带时填写，否则为 null（现有四种来源均不带置信度）。
- compact 是摘录式压缩：只摘录原文（截断标注 …），不做抽象改写。
- 决策/疑问标记词表为内置常量（见下），rename 只影响之后生成的 handoff.md。
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, append_jsonl, new_run_id, now_iso,
    read_json, read_jsonl, sha256_file, sha256_text, write_json,
)
from .ingest import parse_docx, read_text_file

UNKNOWN_SPEAKER = "UNKNOWN"

# 决策/疑问标记词表：自研启发式常量（非数据文件，随版本固定，保证跨环境确定性）。
DECISION_WORDS = ("决定", "就这么定", "结论", "拍板")
QUESTION_WORDS = ("？", "?", "吗", "呢", "如何")

SUPPORTED_FORMATS = ("srt", "vtt", "txt", "md", "docx")
_EXT_MAP = {".srt": "srt", ".vtt": "vtt", ".txt": "txt", ".md": "md",
            ".markdown": "md", ".docx": "docx"}

_RE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_MTYPE = re.compile(r"^[\w\-一-鿿]+$")
# 讯飞风格：[00:01:02] 说话人1：文本（时间可省略小时位，冒号半/全角均可）
_RE_IFLYTEK = re.compile(
    r"^\s*\[(\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?)\]\s*"
    r"([^\s:：\[\]]{1,20})\s*[:：]\s*(.*)$"
)
# Zoom VTT 说话人前缀：行首「姓名: 」（冒号后须跟空白，避免把「结论：就这么办」误当说话人）
_RE_VTT_SPEAKER = re.compile(r"^([^\s:：]{1,20})[:：]\s+(.*)$")
# SRT/VTT 时间轴行（用于定位 source_line）
_RE_TS_LINE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?[,.]\d{1,3}\s*-->")
# VTT 时间戳 → 秒（支持 HH:MM:SS.mmm 与 MM:SS.mmm）
_RE_VTT_TS = re.compile(r"^(?:(\d{1,2}):)?(\d{2}):(\d{2})[,.](\d{1,3})$")


# ---------- 目录骨架 ----------

def _meeting_dir(ws: Workspace, meeting_id: str) -> Path:
    mdir = ws.path("meetings", meeting_id)
    if not mdir.is_dir():
        raise WritersRoomError(
            f"会议不存在：{meeting_id}",
            "先运行 writersroom meeting locate --date <YYYY-MM-DD> --type <类型> 创建骨架",
        )
    return mdir


def locate(ws: Workspace, date: str, mtype: str) -> Path:
    """定位/创建 meetings/<date>_<mtype>/ 骨架（raw/runs/review），返回会议目录。"""
    ws.require()
    if not _RE_DATE.match(date):
        raise WritersRoomError(f"日期格式非法：{date!r}", "日期须为 YYYY-MM-DD，如 2026-08-26")
    if not mtype or not _RE_MTYPE.match(mtype):
        raise WritersRoomError(
            f"会议类型非法：{mtype!r}",
            "类型只允许中英文、数字、-、_，如 plan / 策划会",
        )
    mdir = ws.path("meetings", f"{date}_{mtype}")
    for sub in ("raw", "runs", "review"):
        mdir.joinpath(sub).mkdir(parents=True, exist_ok=True)
    return mdir


def list_meetings(ws: Workspace) -> list[dict]:
    """扫描 meetings/，返回每个会议各层是否存在。"""
    ws.require()
    root = ws.path("meetings")
    out: list[dict] = []
    if not root.is_dir():
        return out
    for mdir in sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name):
        raw = mdir / "raw"
        runs = mdir / "runs"
        out.append({
            "meeting_id": mdir.name,
            "raw": raw.is_dir() and any(f.name != "provenance.json" for f in raw.iterdir()),
            "normalized": mdir.joinpath("normalized.json").exists(),
            "runs": sorted(p.name for p in runs.iterdir() if p.is_dir()) if runs.is_dir() else [],
            "review": mdir.joinpath("review", "overrides.json").exists()
                      or bool(read_jsonl(mdir / "review" / "history.jsonl")),
            "handoff": mdir.joinpath("handoff.md").exists(),
        })
    return out


# ---------- 导入（raw 层） ----------

def _detect_format(path: Path) -> str:
    fmt = _EXT_MAP.get(path.suffix.lower())
    if not fmt:
        raise WritersRoomError(
            f"不支持的会议文件类型：{path.name}",
            f"支持：{', '.join(SUPPORTED_FORMATS)}",
        )
    return fmt


def ingest_meeting(ws: Workspace, meeting_id: str, file: str | Path) -> dict:
    """复制文件到 raw/（同名不覆盖），并更新 raw/provenance.json。返回该文件条目。"""
    ws.require()
    mdir = _meeting_dir(ws, meeting_id)
    src = Path(file)
    if not src.exists():
        raise WritersRoomError(f"文件不存在：{src}", "检查路径；相对路径以当前目录为基准")
    fmt = _detect_format(src)
    dest = mdir / "raw" / src.name
    if dest.exists():
        raise WritersRoomError(
            f"raw/ 下已存在同名文件：{src.name}",
            "请改名后重新导入；raw/ 原始证据不可覆盖",
        )
    mdir.joinpath("raw").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    entry = {
        "name": src.name,
        "format": fmt,
        "sha256": sha256_file(dest),
        "imported_at": now_iso(),
        "tool": TOOL,
    }
    prov_path = mdir / "raw" / "provenance.json"
    prov = read_json(prov_path) if prov_path.exists() else {
        "schema": 1, "meeting_id": meeting_id, "files": [],
    }
    prov["files"].append(entry)
    write_json(prov_path, prov)
    return {
        "meeting_id": meeting_id,
        "name": entry["name"],
        "format": fmt,
        "sha256": entry["sha256"],
        "path": ws.rel(dest),
    }


# ---------- 规范化（normalized.json） ----------

def _hms_to_seconds(ts: str) -> float:
    """'HH:MM:SS[.mmm]' / 'MM:SS' → 秒。"""
    m = _RE_VTT_TS.match(ts.strip())
    if m:
        h = int(m.group(1) or 0)
        frac = m.group(4).ljust(3, "0")[:3]
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(frac) / 1000
    parts = [int(p) for p in ts.strip().split(":")]
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    return float(parts[0] * 3600 + parts[1] * 60 + parts[2])


def _blocks_with_lines(text: str) -> list[tuple[int, list[str]]]:
    """按空行分块，返回 [(起始行号（1 起）, 行列表)]，用于推导 source_line。"""
    blocks: list[tuple[int, list[str]]] = []
    cur: list[str] = []
    start: int | None = None
    for i, ln in enumerate(text.splitlines(), start=1):
        if ln.strip():
            if start is None:
                start = i
            cur.append(ln)
        elif cur:
            blocks.append((start, cur))
            cur, start = [], None
    if cur:
        blocks.append((start, cur))
    return blocks


def _cue_block_lines(text: str) -> list[int]:
    """含时间轴行（-->）的块起始行号列表，与库解析出的 cue 按序一一对应。"""
    return [start for start, lines in _blocks_with_lines(text)
            if any(_RE_TS_LINE.search(ln) for ln in lines)]


def _parse_srt(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        import srt
    except ImportError as e:  # 硬依赖，防御性处理
        raise WritersRoomError("缺少 srt 库", "pip install 'srt>=3.5.3'") from e
    text, warnings = read_text_file(path)
    try:
        subs = list(srt.parse(text))
    except Exception as e:
        raise WritersRoomError(
            f"SRT 解析失败：{path.name}（{e}）",
            "检查文件是否为合法 SRT；必要时先转为纯 TXT 再导入",
        ) from e
    block_lines = _cue_block_lines(text)
    turns = []
    for i, sub in enumerate(subs):
        turns.append({
            "start": sub.start.total_seconds(),
            "end": sub.end.total_seconds(),
            "speaker": UNKNOWN_SPEAKER,  # SRT 无说话人约定，严禁猜测
            "text": sub.content.strip(),
            "confidence": None,
            "source_format": "srt",
            "source_line": block_lines[i] if i < len(block_lines) else None,
        })
    return turns, warnings


def _parse_vtt(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        import webvtt
    except ImportError as e:  # 硬依赖，防御性处理
        raise WritersRoomError("缺少 webvtt-py 库", "pip install 'webvtt-py>=0.5.1'") from e
    try:
        captions = list(webvtt.read(str(path)))
    except Exception as e:
        raise WritersRoomError(
            f"VTT 解析失败：{path.name}（{e}）",
            "检查文件是否为合法 WebVTT（UTF-8 编码）；必要时先转为纯 TXT 再导入",
        ) from e
    text, warnings = read_text_file(path)
    block_lines = _cue_block_lines(text)
    turns = []
    for i, cue in enumerate(captions):
        raw_text = cue.text.strip()
        first, _, rest = raw_text.partition("\n")
        speaker = UNKNOWN_SPEAKER
        m = _RE_VTT_SPEAKER.match(first)
        if m:
            speaker = m.group(1).strip()
            raw_text = (m.group(2) + ("\n" + rest if rest else "")).strip()
        turns.append({
            "start": float(cue.start_in_seconds),
            "end": float(cue.end_in_seconds),
            "speaker": speaker,
            "text": raw_text,
            "confidence": None,
            "source_format": "vtt",
            "source_line": block_lines[i] if i < len(block_lines) else None,
        })
    return turns, warnings


def _parse_iflytek(text: str) -> tuple[list[dict], list[dict]]:
    """讯飞风格 TXT：[时间] 说话人N：文本；不匹配的非空行并入上一条（换行续行）。"""
    turns: list[dict] = []
    warnings: list[dict] = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue  # # 开头为注释行（如合成样例声明）
        m = _RE_IFLYTEK.match(line)
        if m:
            turns.append({
                "start": _hms_to_seconds(m.group(1)),
                "end": None,  # 该格式只有起点时间
                "speaker": m.group(2).strip(),  # 保留源标签「说话人N」，不猜测实名
                "text": m.group(3).strip(),
                "confidence": None,
                "source_format": "iflytek_txt",
                "source_line": i,
            })
        elif turns:
            turns[-1]["text"] += "\n" + line.strip()
        else:
            warnings.append({
                "code": "UNPARSED_LINE",
                "detail": f"第 {i} 行不符合讯飞格式且无前续发言，已跳过",
                "location": i,
            })
    return turns, warnings


def _parse_plain_lines(text: str, source_format: str) -> list[dict]:
    """纯 TXT/MD：每个非空行一个 turn，无时间戳、UNKNOWN 说话人。"""
    turns = []
    for i, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        turns.append({
            "start": None,
            "end": None,
            "speaker": UNKNOWN_SPEAKER,
            "text": line,
            "confidence": None,
            "source_format": source_format,
            "source_line": i,
        })
    return turns


def _parse_file(path: Path, fmt: str) -> tuple[list[dict], list[dict]]:
    if fmt == "srt":
        return _parse_srt(path)
    if fmt == "vtt":
        return _parse_vtt(path)
    if fmt == "docx":
        blocks, warnings = parse_docx(path)  # 复用 ingest 层
        turns = [{
            "start": None, "end": None, "speaker": UNKNOWN_SPEAKER,
            "text": b["text"], "confidence": None,
            "source_format": "docx", "source_line": b["line_start"],
        } for b in blocks]
        return turns, warnings
    # txt / md：txt 先嗅探是否讯飞风格
    text, warnings = read_text_file(path)
    if fmt == "txt" and any(_RE_IFLYTEK.match(ln) for ln in text.splitlines()):
        t, w = _parse_iflytek(text)
        return t, warnings + w
    return _parse_plain_lines(text, fmt), warnings


def normalize(ws: Workspace, meeting_id: str) -> dict:
    """解析 raw/ 全部文件为统一 turns（schema 1），写 normalized.json 并返回该文档。"""
    ws.require()
    mdir = _meeting_dir(ws, meeting_id)
    raw = mdir / "raw"
    files = sorted((f for f in raw.iterdir() if f.is_file() and f.name != "provenance.json"),
                   key=lambda f: f.name) if raw.is_dir() else []
    if not files:
        raise WritersRoomError(
            f"会议 {meeting_id} 的 raw/ 为空",
            "先用 writersroom meeting ingest <file> 导入转写/笔记文件",
        )
    turns: list[dict] = []
    warnings: list[dict] = []
    hashes: list[str] = []
    for f in files:
        fmt = _detect_format(f)
        file_turns, file_warnings = _parse_file(f, fmt)
        turns.extend(file_turns)
        warnings.extend(file_warnings)
        hashes.append(f"{f.name}:{sha256_file(f)}")
    for seq, turn in enumerate(turns):
        turn["seq"] = seq
    doc = {
        "schema": 1,
        "meeting_id": meeting_id,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "source_hash": sha256_text("\n".join(hashes)),
        "turns": turns,
        "warnings": warnings,
    }
    write_json(mdir / "normalized.json", doc)
    return doc


# ---------- review 层（覆盖与历史） ----------

def _empty_overrides() -> dict:
    return {"schema": 1, "speaker_map": {}, "candidates": {}}


def _load_overrides(mdir: Path) -> dict:
    path = mdir / "review" / "overrides.json"
    return read_json(path) if path.exists() else _empty_overrides()


def _apply_entry(overrides: dict, entry: dict) -> None:
    if entry["action"] == "rename_speaker":
        overrides["speaker_map"][entry["old"]] = entry["new"]
    elif entry["action"] == "accept_candidate":
        overrides["candidates"][entry["id"]] = {
            "status": "accepted", "updated_at": entry["created_at"],
        }
    elif entry["action"] == "reject_candidate":
        overrides["candidates"][entry["id"]] = {
            "status": "rejected", "updated_at": entry["created_at"],
        }


def review(ws: Workspace, meeting_id: str, action: str, **kw) -> dict:
    """人工覆盖：rename_speaker(old,new) / accept_candidate(id) / reject_candidate(id)。

    写 review/overrides.json 并 append review/history.jsonl（seq 自增、actor="human"）。
    只影响之后生成的 handoff；raw/normalized 永不被修改。
    """
    ws.require()
    mdir = _meeting_dir(ws, meeting_id)
    overrides = _load_overrides(mdir)
    history = read_jsonl(mdir / "review" / "history.jsonl")
    entry: dict = {
        "seq": len(history) + 1,
        "action": action,
        "actor": "human",
        "created_at": now_iso(),
    }
    if action == "rename_speaker":
        old, new = kw.get("old"), kw.get("new")
        if not old or not new:
            raise WritersRoomError("rename_speaker 需要 old 与 new 参数",
                                   '例：writersroom meeting review … rename_speaker --old 说话人1 --new 林晓岚')
        if old == new:
            raise WritersRoomError("新旧说话人名相同，无需更名", "确认 --old 与 --new 参数")
        npath = mdir / "normalized.json"
        if not npath.exists():
            raise WritersRoomError("尚未 normalize，无法校验说话人",
                                   "先运行 writersroom meeting normalize")
        speakers = sorted({t["speaker"] for t in read_json(npath)["turns"]})
        known = set(speakers) | set(overrides["speaker_map"]) | set(overrides["speaker_map"].values())
        if old not in known:
            raise WritersRoomError(
                f"说话人不存在：{old}",
                f"当前说话人：{', '.join(speakers)}；更名对象须来自该列表或已有映射",
            )
        entry["old"], entry["new"] = old, new
    elif action in ("accept_candidate", "reject_candidate"):
        cid = kw.get("id")
        if not cid:
            raise WritersRoomError(f"{action} 需要 id 参数",
                                   "候选 id 见最新 runs/<run_id>/segments.json 的 candidates")
        known = _latest_candidate_ids(mdir)
        if not known:
            raise WritersRoomError("尚无 compact 结果，无法定位候选结论",
                                   "先运行 writersroom meeting compact")
        if cid not in known:
            raise WritersRoomError(
                f"未知候选 id：{cid}",
                f"当前候选：{', '.join(known)}（见最新 compact run）",
            )
        entry["id"] = cid
    else:
        raise WritersRoomError(
            f"未知 review 动作：{action}",
            "支持：rename_speaker / accept_candidate / reject_candidate",
        )
    _apply_entry(overrides, entry)
    write_json(mdir / "review" / "overrides.json", overrides)
    append_jsonl(mdir / "review" / "history.jsonl", entry)
    return overrides


def restore(ws: Workspace, meeting_id: str, seq: int) -> dict:
    """按 history.jsonl 前 seq 条重放重建 overrides.json，返回新 overrides。"""
    ws.require()
    mdir = _meeting_dir(ws, meeting_id)
    history = read_jsonl(mdir / "review" / "history.jsonl")
    if seq < 0 or seq > len(history):
        raise WritersRoomError(
            f"restore 序号越界：{seq}（历史共 {len(history)} 条）",
            f"--seq 取 0..{len(history)}；0 表示回到无任何覆盖的初始状态",
        )
    overrides = _empty_overrides()
    for entry in history[:seq]:
        _apply_entry(overrides, entry)
    write_json(mdir / "review" / "overrides.json", overrides)
    return overrides


def _latest_run_dir(mdir: Path) -> Path | None:
    runs = mdir / "runs"
    if not runs.is_dir():
        return None
    ids = sorted(p.name for p in runs.iterdir() if p.is_dir())
    return runs / ids[-1] if ids else None


def _latest_candidate_ids(mdir: Path) -> list[str]:
    rdir = _latest_run_dir(mdir)
    if not rdir:
        return []
    seg_path = rdir / "segments.json"
    if not seg_path.exists():
        return []
    return [c["id"] for c in read_json(seg_path).get("candidates", [])]


# ---------- compact（分段摘录 + handoff.md） ----------

def _resolve_speaker(name: str, speaker_map: dict) -> str:
    """沿更名链解析（A→B→C），带环保护。"""
    seen = set()
    while name in speaker_map and name not in seen:
        seen.add(name)
        name = speaker_map[name]
    return name


def _merge_segments(turns: list[dict], speaker_map: dict) -> list[dict]:
    """连续同说话人 turn 合并为段；UNKNOWN 不合并（未知说话人不能假定是同一人）。"""
    segments: list[dict] = []
    for t in turns:
        display = _resolve_speaker(t["speaker"], speaker_map)
        can_merge = (segments and display != UNKNOWN_SPEAKER
                     and segments[-1]["speaker"] == display)
        if can_merge:
            seg = segments[-1]
            seg["text"] += "\n" + t["text"]
            seg["turn_seqs"].append(t["seq"])
            if t["end"] is not None:
                seg["end"] = t["end"]
            if t["speaker"] not in seg["speaker_raw"]:
                seg["speaker_raw"].append(t["speaker"])
        else:
            segments.append({
                "seq": len(segments),
                "start": t["start"],
                "end": t["end"],
                "speaker": display,
                "speaker_raw": [t["speaker"]],
                "turn_seqs": [t["seq"]],
                "text": t["text"],
            })
    for seg in segments:
        markers = []
        if any(w in seg["text"] for w in DECISION_WORDS):
            markers.append("决策")
        if any(w in seg["text"] for w in QUESTION_WORDS):
            markers.append("疑问")
        if UNKNOWN_SPEAKER in seg["speaker_raw"] or seg["start"] is None:
            markers.append("?")
        seg["markers"] = markers
    return segments


def _excerpt(text: str, limit: int = 60) -> str:
    one_line = " ".join(text.split())
    return one_line[:limit] + "…" if len(one_line) > limit else one_line


def _fmt_time(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    total = int(seconds)
    return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"


_STATUS_ZH = {"candidate": "候选", "accepted": "已接受", "rejected": "已否决"}


def compact(ws: Workspace, meeting_id: str) -> Path:
    """连续同说话人 turn 合并为段 → runs/<run_id>/segments.json + handoff.md。

    返回本次 run 目录。重跑生成新 run_id，不覆盖旧 run；handoff.md 始终为最新生成。
    """
    ws.require()
    mdir = _meeting_dir(ws, meeting_id)
    npath = mdir / "normalized.json"
    if not npath.exists():
        raise WritersRoomError(
            f"会议 {meeting_id} 尚未 normalize",
            "先运行 writersroom meeting normalize",
        )
    norm_text = npath.read_text(encoding="utf-8")
    doc = read_json(npath)
    overrides = _load_overrides(mdir)
    segments = _merge_segments(doc["turns"], overrides["speaker_map"])
    candidates = [{
        "id": f"cand-{seg['turn_seqs'][0]}",
        "segment_seq": seg["seq"],
        "start": seg["start"],
        "speaker": seg["speaker"],
        "excerpt": _excerpt(seg["text"]),
        "status": overrides["candidates"].get(f"cand-{seg['turn_seqs'][0]}", {}).get(
            "status", "candidate"),
    } for seg in segments if "决策" in seg["markers"]]

    run_id = new_run_id({p.name for p in (mdir / "runs").iterdir() if p.is_dir()}
                        if (mdir / "runs").is_dir() else set())
    run_dir = mdir / "runs" / run_id
    run_dir.mkdir(parents=True)
    created_at = now_iso()
    write_json(run_dir / "segments.json", {
        "schema": 1,
        "meeting_id": meeting_id,
        "run_id": run_id,
        "produced_by": TOOL,
        "created_at": created_at,
        "source_hash": sha256_text(
            norm_text + "\n" + json.dumps(overrides, ensure_ascii=False, sort_keys=True)
        ),
        "segments": segments,
        "candidates": candidates,
    })

    lines = [
        f"# 会议交接：{meeting_id}",
        "",
        f"- 生成：{created_at}（run_id {run_id}，工具 {TOOL}）",
        f"- 来源：normalized.json 共 {len(doc['turns'])} 条发言，合并为 {len(segments)} 段",
        "- 说明：本文件为摘录式压缩（未做抽象改写）；[决策]/[疑问] 为词表命中，[?] 表示说话人未知或无时间戳",
        "",
        "## 分段摘录",
        "",
    ]
    for seg in segments:
        mark = "".join(f" [{m}]" for m in seg["markers"])
        lines.append(
            f"- [{_fmt_time(seg['start'])}] {seg['speaker']}: {_excerpt(seg['text'])}{mark}"
        )
    lines += ["", "## 未决问题", ""]
    questions = [s for s in segments if "疑问" in s["markers"]]
    if questions:
        for seg in questions:
            lines.append(f"- [{_fmt_time(seg['start'])}] {seg['speaker']}: {_excerpt(seg['text'])}")
    else:
        lines.append("- （无）")
    lines += ["", "## 候选结论", ""]
    if candidates:
        for c in candidates:
            lines.append(
                f"- {c['id']} [{_fmt_time(c['start'])}] {c['speaker']}: {c['excerpt']}"
                f"（状态：{_STATUS_ZH.get(c['status'], c['status'])}）"
            )
    else:
        lines.append("- （无）")
    (mdir / "handoff.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return run_dir
