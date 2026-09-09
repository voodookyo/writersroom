"""kb 测试：命中排序、元数据加权、确定性、无结果明示、Workspace 入参。"""
import shutil
from pathlib import Path

import pytest

from writersroom import kb
from writersroom.core import Workspace, WritersRoomError

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
KB_DIR = SAMPLES / "knowledge"


def test_search_hits_and_order():
    r = kb.search(KB_DIR, ["爆破"])
    assert r["indexed_files"] == 3
    assert r["hits"], "应命中类型片制作成本笔记"
    assert r["hits"][0]["file"] == "类型片制作成本笔记.md"
    assert r["hits"][0]["matched_terms"] == ["爆破"]
    assert r["hits"][0]["context"], "命中应带 ±1 行摘录"
    scores = [h["score"] for h in r["hits"]]
    assert scores == sorted(scores, reverse=True)


def test_search_multi_term():
    r = kb.search(KB_DIR, ["钩子", "反转"])
    assert r["hits"][0]["file"] == "悬疑剧节奏笔记.md"
    assert set(r["hits"][0]["matched_terms"]) == {"钩子", "反转"}


def test_search_deterministic_same_input_same_score():
    r1 = kb.search(KB_DIR, ["成本", "场面"])
    r2 = kb.search(KB_DIR, ["成本", "场面"])
    h1 = [(h["file"], h["score"]) for h in r1["hits"]]
    h2 = [(h["file"], h["score"]) for h in r2["hits"]]
    assert h1 == h2


def test_title_tags_boosted(tmp_path):
    # 正文相同、A 在正文出现一次检索词，B 仅在 frontmatter title 出现 → B 加权 ×2 应排前
    body = "灯塔立在河口。\n\n船工们每天经过这里，讨论天气与收成。\n"
    (tmp_path / "a.md").write_text("---\ntitle: 渡口杂记\ntags: [渡口]\n---\n\n" + body, encoding="utf-8")
    (tmp_path / "b.md").write_text("---\ntitle: 灯塔\ntags: [笔记]\n---\n\n" + body.replace("灯塔立在河口。", "它立在河口。"), encoding="utf-8")
    r = kb.search(tmp_path, ["灯塔"])
    assert len(r["hits"]) == 2
    assert r["hits"][0]["file"] == "b.md"


def test_no_hit_message_and_indexed_count():
    r = kb.search(KB_DIR, ["量子计算机"])
    assert r["hits"] == []
    assert r["message"] == "未命中"
    assert r["indexed_files"] == 3


def test_search_accepts_workspace(tmp_path):
    ws = Workspace.init(tmp_path / "ws")
    for md in KB_DIR.glob("*.md"):
        shutil.copyfile(md, ws.path("knowledge", md.name))
    r = kb.search(ws, ["航拍"])
    assert r["indexed_files"] == 3
    assert r["hits"][0]["file"] == "类型片制作成本笔记.md"


def test_search_missing_dir_errors(tmp_path):
    with pytest.raises(WritersRoomError) as exc:
        kb.search(tmp_path / "不存在", ["词"])
    assert exc.value.hint


def test_search_empty_terms_errors():
    with pytest.raises(WritersRoomError):
        kb.search(KB_DIR, ["", "  "])
