"""会议证据管线集成测试（spec §4.6 + §6 验收映射）。

覆盖：四格式 normalize 的 turn 数与字段、UNKNOWN 保留、review 覆盖、
restore 重建、handoff 时间戳与 [决策]/[?] 标记、runs 重跑不覆盖。
样例均为 samples/ 下自造合成文件（虚构人名）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from writersroom import meetings
from writersroom.core import WritersRoomError, Workspace, read_json, read_jsonl

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

DATE, MTYPE = "2026-08-20", "策划会"
MID = f"{DATE}_{MTYPE}"

TURN_FIELDS = {"seq", "start", "end", "speaker", "text", "confidence",
               "source_format", "source_line"}


@pytest.fixture()
def ws(tmp_path):
    return Workspace.init(tmp_path)


@pytest.fixture()
def meeting(ws):
    meetings.locate(ws, DATE, MTYPE)
    return ws


def _ingest_and_normalize(ws, sample: str):
    meetings.ingest_meeting(ws, MID, SAMPLES / sample)
    return meetings.normalize(ws, MID)


# ---------- locate / list / ingest ----------

def test_locate_creates_skeleton(ws):
    mdir = meetings.locate(ws, DATE, MTYPE)
    assert mdir == ws.path("meetings", MID)
    for sub in ("raw", "runs", "review"):
        assert mdir.joinpath(sub).is_dir()
    # 重复 locate 幂等
    assert meetings.locate(ws, DATE, MTYPE) == mdir


def test_locate_bad_date(ws):
    with pytest.raises(WritersRoomError):
        meetings.locate(ws, "2026/08/20", MTYPE)


def test_list_meetings_layers(ws):
    assert meetings.list_meetings(ws) == []
    meetings.locate(ws, DATE, MTYPE)
    (info,) = meetings.list_meetings(ws)
    assert info["meeting_id"] == MID
    assert info["raw"] is False and info["normalized"] is False
    assert info["runs"] == [] and info["handoff"] is False
    _ingest_and_normalize(ws, "meeting.srt")
    (info,) = meetings.list_meetings(ws)
    assert info["raw"] is True and info["normalized"] is True


def test_ingest_requires_locate(ws):
    with pytest.raises(WritersRoomError):
        meetings.ingest_meeting(ws, MID, SAMPLES / "meeting.srt")


def test_ingest_same_name_no_overwrite(meeting):
    entry = meetings.ingest_meeting(meeting, MID, SAMPLES / "meeting.srt")
    assert entry["format"] == "srt" and len(entry["sha256"]) == 64
    with pytest.raises(WritersRoomError) as ei:
        meetings.ingest_meeting(meeting, MID, SAMPLES / "meeting.srt")
    assert "改名" in ei.value.hint
    prov = read_json(meeting.path("meetings", MID, "raw", "provenance.json"))
    assert [f["name"] for f in prov["files"]] == ["meeting.srt"]


def test_ingest_unsupported_ext(meeting, tmp_path):
    bad = tmp_path / "notes.xyz"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(WritersRoomError):
        meetings.ingest_meeting(meeting, MID, bad)


# ---------- normalize：四种格式 ----------

def test_normalize_zoom_vtt(meeting):
    doc = _ingest_and_normalize(meeting, "meeting_zoom.vtt")
    turns = doc["turns"]
    assert len(turns) == 7
    assert all(set(t) == TURN_FIELDS for t in turns)
    assert [t["seq"] for t in turns] == list(range(7))
    assert all(t["source_format"] == "vtt" for t in turns)
    assert all(t["confidence"] is None for t in turns)
    # Zoom「姓名: 」前缀抽取
    assert turns[0]["speaker"] == "林晓岚"
    assert turns[0]["text"].startswith("我们今天过一下")
    assert turns[2]["speaker"] == "林晓岚" and "决定" in turns[2]["text"]
    # 无说话人前缀的 cue → UNKNOWN，严禁猜测
    assert turns[6]["speaker"] == "UNKNOWN"
    # 时间戳为秒
    assert turns[0]["start"] == 1.0 and turns[0]["end"] == 5.0
    assert turns[1]["start"] == 6.0
    # source_line 指向 cue 块起始行（1 起）
    assert all(isinstance(t["source_line"], int) and t["source_line"] >= 1 for t in turns)
    assert doc["schema"] == 1 and doc["produced_by"].startswith("writersroom")
    assert len(doc["source_hash"]) == 64


def test_normalize_srt_all_unknown(meeting):
    doc = _ingest_and_normalize(meeting, "meeting.srt")
    turns = doc["turns"]
    assert len(turns) == 4
    assert all(t["speaker"] == "UNKNOWN" for t in turns)  # SRT 无说话人
    assert all(t["source_format"] == "srt" for t in turns)
    assert all(t["confidence"] is None for t in turns)
    assert turns[1]["start"] == 5.0 and turns[1]["end"] == 9.0
    assert "结论" in turns[1]["text"]
    assert all(t["source_line"] for t in turns)


def test_normalize_iflytek_txt(meeting):
    doc = _ingest_and_normalize(meeting, "meeting_iflytek.txt")
    turns = doc["turns"]
    assert len(turns) == 5
    assert all(t["source_format"] == "iflytek_txt" for t in turns)
    # 原标签保留，不猜测实名
    assert [t["speaker"] for t in turns] == ["说话人1", "说话人2", "说话人1", "说话人2", "说话人1"]
    assert turns[0]["start"] == 62.0 and turns[0]["end"] is None
    assert turns[3]["start"] == 123.0
    assert turns[0]["source_line"] == 2  # 第 1 行为 # 合成样例声明注释
    assert doc["warnings"] == []


def test_normalize_plain_txt(meeting):
    doc = _ingest_and_normalize(meeting, "meeting_notes.txt")
    turns = doc["turns"]
    assert len(turns) == 5  # 每个非空行一个 turn
    assert all(t["speaker"] == "UNKNOWN" for t in turns)
    assert all(t["start"] is None and t["end"] is None for t in turns)
    assert all(t["source_format"] == "txt" for t in turns)
    assert [t["source_line"] for t in turns] == [1, 2, 3, 4, 5]


def test_normalize_multi_file(meeting):
    meetings.ingest_meeting(meeting, MID, SAMPLES / "meeting.srt")
    meetings.ingest_meeting(meeting, MID, SAMPLES / "meeting_zoom.vtt")
    doc = meetings.normalize(meeting, MID)
    assert len(doc["turns"]) == 11  # 4 + 7，seq 全局连续
    assert [t["seq"] for t in doc["turns"]] == list(range(11))
    # normalized.json 已落盘且内容一致
    on_disk = read_json(meeting.path("meetings", MID, "normalized.json"))
    assert on_disk["turns"] == doc["turns"]


def test_normalize_empty_raw(ws):
    meetings.locate(ws, DATE, MTYPE)
    with pytest.raises(WritersRoomError):
        meetings.normalize(ws, MID)


# ---------- compact：分段、handoff、候选 ----------

def test_compact_segments_and_handoff(meeting):
    _ingest_and_normalize(meeting, "meeting_zoom.vtt")
    run_dir = meetings.compact(meeting, MID)
    seg_doc = read_json(run_dir / "segments.json")
    # 林晓岚连续两条（turn 2/3）合并 → 6 段
    assert len(seg_doc["segments"]) == 6
    merged = seg_doc["segments"][2]
    assert merged["speaker"] == "林晓岚" and merged["turn_seqs"] == [2, 3]
    assert "决定" in merged["text"] and "决策" in merged["markers"]
    # 候选结论来自 [决策] 段，id 稳定（cand-<首 turn seq>）
    cand_ids = [c["id"] for c in seg_doc["candidates"]]
    assert cand_ids == ["cand-2", "cand-5"]
    assert all(c["status"] == "candidate" for c in seg_doc["candidates"])

    handoff = meeting.path("meetings", MID, "handoff.md").read_text(encoding="utf-8")
    assert "[00:00:01] 林晓岚:" in handoff  # 时间戳 HH:MM:SS
    assert "[决策]" in handoff and "[疑问]" in handoff
    assert "[?]" in handoff  # UNKNOWN 段
    assert "## 未决问题" in handoff and "## 候选结论" in handoff
    assert "cand-2" in handoff and "（状态：候选）" in handoff


def test_compact_no_timestamp_marks(meeting):
    _ingest_and_normalize(meeting, "meeting_notes.txt")
    meetings.compact(meeting, MID)
    handoff = meeting.path("meetings", MID, "handoff.md").read_text(encoding="utf-8")
    # 纯文本无时间戳、UNKNOWN：每段标 [?]，时间位为 ?
    excerpt_section = handoff.split("## 分段摘录")[1].split("## 未决问题")[0]
    assert excerpt_section.count("[?] UNKNOWN:") == 5
    assert "如何分工还需确认" in handoff  # 疑问词「如何」命中 → 进入未决问题
    question_section = handoff.split("## 未决问题")[1]
    assert "如何分工" in question_section


def test_compact_runs_no_overwrite(meeting):
    _ingest_and_normalize(meeting, "meeting.srt")
    run1 = meetings.compact(meeting, MID)
    run2 = meetings.compact(meeting, MID)
    assert run1 != run2 and run1.is_dir() and run2.is_dir()
    assert (run1 / "segments.json").exists() and (run2 / "segments.json").exists()
    runs = sorted(p.name for p in meeting.path("meetings", MID, "runs").iterdir())
    assert runs == sorted([run1.name, run2.name])


# ---------- review / restore ----------

def test_rename_speaker_affects_handoff_not_normalized(meeting):
    doc = _ingest_and_normalize(meeting, "meeting_iflytek.txt")
    meetings.compact(meeting, MID)
    meetings.review(meeting, MID, "rename_speaker", old="说话人1", new="林晓岚")
    meetings.compact(meeting, MID)
    handoff = meeting.path("meetings", MID, "handoff.md").read_text(encoding="utf-8")
    assert "[00:01:02] 林晓岚:" in handoff
    assert "说话人1" not in handoff.split("## 分段摘录")[1].split("## 未决问题")[0]
    # normalized.json 不被 review 修改
    after = read_json(meeting.path("meetings", MID, "normalized.json"))
    assert after["turns"] == doc["turns"]
    assert after["turns"][0]["speaker"] == "说话人1"


def test_rename_unknown_speaker_rejected(meeting):
    _ingest_and_normalize(meeting, "meeting_iflytek.txt")
    with pytest.raises(WritersRoomError) as ei:
        meetings.review(meeting, MID, "rename_speaker", old="不存在的人", new="x")
    assert "说话人1" in ei.value.hint


def test_review_history_and_overrides(meeting):
    _ingest_and_normalize(meeting, "meeting_zoom.vtt")
    meetings.compact(meeting, MID)
    meetings.review(meeting, MID, "rename_speaker", old="周牧", new="周导")
    meetings.review(meeting, MID, "accept_candidate", id="cand-2")
    meetings.review(meeting, MID, "reject_candidate", id="cand-5")
    ov = read_json(meeting.path("meetings", MID, "review", "overrides.json"))
    assert ov["speaker_map"] == {"周牧": "周导"}
    assert ov["candidates"]["cand-2"]["status"] == "accepted"
    assert ov["candidates"]["cand-5"]["status"] == "rejected"
    history = read_jsonl(meeting.path("meetings", MID, "review", "history.jsonl"))
    assert [h["seq"] for h in history] == [1, 2, 3]
    assert all(h["actor"] == "human" for h in history)
    # 候选状态反映到下一次 handoff
    meetings.compact(meeting, MID)
    handoff = meeting.path("meetings", MID, "handoff.md").read_text(encoding="utf-8")
    assert "cand-2" in handoff and "（状态：已接受）" in handoff
    assert "（状态：已否决）" in handoff


def test_review_candidate_requires_compact(meeting):
    _ingest_and_normalize(meeting, "meeting_iflytek.txt")
    with pytest.raises(WritersRoomError) as ei:
        meetings.review(meeting, MID, "accept_candidate", id="cand-2")
    assert "compact" in ei.value.hint
    meetings.compact(meeting, MID)
    with pytest.raises(WritersRoomError):
        meetings.review(meeting, MID, "accept_candidate", id="cand-999")


def test_review_unknown_action(meeting):
    with pytest.raises(WritersRoomError):
        meetings.review(meeting, MID, "delete_everything")


def test_restore_replays_history(meeting):
    _ingest_and_normalize(meeting, "meeting_iflytek.txt")
    meetings.compact(meeting, MID)
    meetings.review(meeting, MID, "rename_speaker", old="说话人1", new="林晓岚")
    meetings.review(meeting, MID, "accept_candidate", id="cand-2")
    meetings.review(meeting, MID, "rename_speaker", old="林晓岚", new="晓岚")
    # 回到第 1 条之后：只有第一次更名
    ov = meetings.restore(meeting, MID, 1)
    assert ov["speaker_map"] == {"说话人1": "林晓岚"}
    assert ov["candidates"] == {}
    on_disk = read_json(meeting.path("meetings", MID, "review", "overrides.json"))
    assert on_disk == ov
    # 回到第 2 条之后：含候选接受，尚无第二次更名
    ov = meetings.restore(meeting, MID, 2)
    assert ov["speaker_map"] == {"说话人1": "林晓岚"}
    assert ov["candidates"]["cand-2"]["status"] == "accepted"
    # seq=0 清空覆盖；越界报错
    assert meetings.restore(meeting, MID, 0) == {
        "schema": 1, "speaker_map": {}, "candidates": {},
    }
    with pytest.raises(WritersRoomError):
        meetings.restore(meeting, MID, 99)
    # history 本身不被 restore 修改
    assert len(read_jsonl(meeting.path("meetings", MID, "review", "history.jsonl"))) == 3
