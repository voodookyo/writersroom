"""analyze 模块测试：stats、signals、run_analysis、确定性不变量。"""
from __future__ import annotations

from pathlib import Path

import pytest

from writersroom import analyze, ingest
from writersroom.core import (
    WritersRoomError, Workspace, now_iso, read_json, sha256_file, write_json,
)

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "mini.fountain"

SIGNAL_KEYS = {"active_action", "conflict", "hooks", "emotion", "high_cost",
               "structure"}


@pytest.fixture()
def ws_doc(tmp_path):
    ws = Workspace.init(tmp_path / "ws", title="合成测试项目")
    source_id, doc = ingest.ingest(ws, SAMPLE)
    return ws, source_id, doc


def make_doc(text: str, kind: str = "action") -> dict:
    """最小合成文档模型（单块）。"""
    return {
        "schema": 1,
        "source": {},
        "warnings": [],
        "blocks": [{"index": 0, "kind": kind, "text": text,
                    "line_start": 1, "line_end": 1,
                    "scene_no": None, "episode": None, "page": None}],
        "text": text,
    }


# ---------- stats ----------

def test_stats_basic(ws_doc):
    ws, _, doc = ws_doc
    stats = analyze.analyze_document(doc, ws)
    assert stats["scenes"] == 5
    assert stats["episodes"] == 2
    assert {"林晓", "周岚"} <= set(stats["dialogue_by_character"])
    assert stats["dialogue_lines"] > 0
    assert stats["dialogue_chars"] == 2
    assert stats["chars_total"] > 0
    assert stats["chars_no_space"] > 0
    assert stats["sentences"] > 0
    assert stats["paragraphs"] > 0
    assert stats["est_pages"] > 0
    assert stats["reading_minutes"] > 0
    assert stats["method"] in ("char_ngram", "jieba")


def test_keywords_invariant(ws_doc):
    _, _, doc = ws_doc
    stats = analyze.analyze_document(doc)
    items = stats["keywords"]["items"]
    assert len(items) <= 20
    scores = [it["score"] for it in items]
    assert scores == sorted(scores, reverse=True)
    assert all(it["term"] and it["tf"] >= 1 for it in items)


def test_analyze_deterministic(ws_doc):
    _, _, doc = ws_doc
    assert analyze.analyze_document(doc) == analyze.analyze_document(doc)
    assert analyze.compute_signals(doc) == analyze.compute_signals(doc)


def test_empty_doc():
    doc = {"text": "", "blocks": []}
    stats = analyze.analyze_document(doc)
    assert stats["scenes"] == 0
    assert stats["keywords"]["items"] == []
    signals = analyze.compute_signals(doc)
    assert set(signals) == SIGNAL_KEYS
    assert all(s["evidence"] == [] for s in signals.values())


# ---------- signals ----------

def test_signals_structure(ws_doc):
    ws, _, doc = ws_doc
    signals = analyze.compute_signals(doc, ws)
    assert set(signals) == SIGNAL_KEYS
    for sig in signals.values():
        assert isinstance(sig["evidence"], list)
    # mini.fountain 合成样例保证：冲突/钩子/高成本各至少一处（spec §5）
    assert signals["conflict"]["total"] >= 1
    assert signals["hooks"]["hits"] >= 1
    assert signals["high_cost"]["hits"] >= 1
    assert signals["structure"]["longest_scene"]["chars"] > 0
    assert set(signals["structure"]["scene_lengths"]) >= {"1", "2", "3", "4", "5"}


def test_emotion_plain_negative():
    sig = analyze.compute_signals(make_doc("她难过。"))["emotion"]
    assert sig["positive"] == 0
    assert sig["negative"] == 1.0
    assert sig["evidence"] == [0]


def test_emotion_degree_boost():
    sig = analyze.compute_signals(make_doc("她非常开心。"))["emotion"]
    assert sig["positive"] == 1.5  # 程度副词「非常」加权（阈值表 degree_boost）
    assert sig["negative"] == 0


def test_emotion_negation_flip():
    sig = analyze.compute_signals(make_doc("他并不高兴。"))["emotion"]
    assert sig["positive"] == 0
    assert sig["negative"] == 1.0  # 否定词「不」使正向词反转为负向


def test_active_action_verb_start():
    doc = {
        "text": "冲进屋子。他慢慢地坐下。",
        "blocks": [
            {"index": 0, "kind": "action", "text": "冲进屋子。", "line_start": 1,
             "line_end": 1, "scene_no": None, "episode": None, "page": None},
            {"index": 1, "kind": "action", "text": "他慢慢地坐下。", "line_start": 2,
             "line_end": 2, "scene_no": None, "episode": None, "page": None},
        ],
    }
    sig = analyze.compute_signals(doc)["active_action"]
    assert sig["hits"] == 1
    assert sig["total_action_blocks"] == 2
    assert sig["ratio"] == 0.5
    assert sig["evidence"] == [0]


# ---------- run_analysis ----------

def _register_version(ws: Workspace, label: str, source_id: str) -> None:
    vj = ws.path("versions.json")
    data = read_json(vj)
    data["versions"].append({"label": label, "source_id": source_id,
                             "note": "", "created_at": now_iso()})
    write_json(vj, data)


def test_run_analysis(ws_doc):
    ws, source_id, _ = ws_doc
    _register_version(ws, "v1", source_id)
    run_dir = analyze.run_analysis(ws, "v1")
    assert analyze.latest_run(ws, "v1") == run_dir
    for name in ("stats.json", "signals.json", "hints.json",
                 "manifest.json", "report.md"):
        assert (run_dir / name).exists(), name

    stats_env = read_json(run_dir / "stats.json")
    assert stats_env["produced_by"].startswith("writersroom")
    assert stats_env["source_hash"] == sha256_file(
        ws.path("normalized", f"{source_id}.json"))
    assert stats_env["stats"]["scenes"] == 5

    manifest = read_json(run_dir / "manifest.json")
    assert manifest["run_id"] == run_dir.name
    assert manifest["inputs"][0]["sha256"] == stats_env["source_hash"]

    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "林晓" in report and "八维开发提示" in report

    hints = read_json(run_dir / "hints.json")
    assert hints["disclaimer"] == "启发式开发提示，非艺术结论或平台认证"
    assert len(hints["dimensions"]) == 8


def test_run_analysis_missing_version(ws_doc):
    ws, source_id, _ = ws_doc
    _register_version(ws, "v1", source_id)
    with pytest.raises(WritersRoomError) as ei:
        analyze.run_analysis(ws, "v9")
    assert "v1" in ei.value.hint  # hint 列出已登记版本，可操作


def test_latest_run_none(ws_doc):
    ws, _, _ = ws_doc
    assert analyze.latest_run(ws, "v1") is None
