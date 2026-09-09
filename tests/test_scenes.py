"""scenes 测试：mini.fountain 提取 5 场且场号正确；空文档明示；run 目录产物。"""
from pathlib import Path

import pytest

from writersroom import ingest, scenes
from writersroom.core import Workspace, WritersRoomError, read_json

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
MINI = SAMPLES / "mini.fountain"


def load_mini_doc() -> dict:
    return ingest.parse_document(MINI)


def test_build_index_five_scenes():
    index = scenes.build_index(load_mini_doc())
    assert index["scene_count"] == 5
    assert [s["scene_no"] for s in index["scenes"]] == ["1", "2", "3", "4", "5"]

    s1 = index["scenes"][0]
    assert s1["heading"].startswith("内景")
    assert s1["source_location"] == {"line_start": 5, "line_end": 19, "page": None}
    assert s1["dialogue_characters"] == ["林晓", "周岚"]
    assert s1["opening_excerpt"].startswith("雨夜")
    assert s1["closing_excerpt"] == "我要查清楚。今晚就开始。"

    # 集标题是场边界：第 3 场收尾不含「第2集」
    s3 = index["scenes"][2]
    assert s3["closing_excerpt"] == "她比我们想的快。"

    s5 = index["scenes"][4]
    assert s5["closing_excerpt"] == "那我就赌你会拉住我。"


def test_excerpt_truncation():
    doc = {"blocks": [
        {"index": 0, "kind": "scene_heading", "text": "内景. 屋 日", "line_start": 1, "line_end": 1,
         "scene_no": None, "episode": None, "page": None},
        {"index": 1, "kind": "action", "text": "长" * 80, "line_start": 2, "line_end": 2,
         "scene_no": None, "episode": None, "page": None},
    ]}
    index = scenes.build_index(doc)
    s = index["scenes"][0]
    assert s["scene_no"] == "1"  # 无场号时回退顺序号
    assert s["opening_excerpt"] == "长" * 50 + "…"
    assert s["closing_excerpt"] == "长" * 50 + "…"


def test_empty_doc_returns_empty_with_message():
    index = scenes.build_index({"blocks": [
        {"index": 0, "kind": "paragraph", "text": "没有场景头的文本。", "line_start": 1,
         "line_end": 1, "scene_no": None, "episode": None, "page": None},
    ]})
    assert index["scene_count"] == 0
    assert index["scenes"] == []
    assert "message" in index


def test_scene_index_run_writes_json_and_md(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    ingest.ingest(ws, MINI, source_id="mini")
    run_dir = scenes.scene_index_run(ws, "mini")
    assert run_dir == ws.path("analysis", "_scenes", "mini", run_dir.name)

    data = read_json(run_dir / "scenes.json")
    assert data["scene_count"] == 5
    assert data["produced_by"].startswith("writersroom")
    assert data["source_hash"]
    md = (run_dir / "scenes.md").read_text(encoding="utf-8")
    assert "| 场号 | 场景头 |" in md
    assert "内景. 老宅客厅 夜 #1#" in md


def test_scene_index_run_empty_still_writes(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    txt = tmp_path / "note.txt"
    txt.write_text("合成样例：没有任何场景头的笔记。", encoding="utf-8")
    ingest.ingest(ws, txt, source_id="note")
    run_dir = scenes.scene_index_run(ws, "note")
    data = read_json(run_dir / "scenes.json")
    assert data["scenes"] == []
    assert "message" in data  # 明示空清单，不伪造


def test_scene_index_run_missing_source_errors(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    with pytest.raises(WritersRoomError) as exc:
        scenes.scene_index_run(ws, "不存在")
    assert exc.value.hint
