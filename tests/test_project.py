"""project 模块测试：档案更新、版本标签、写前锁定（fixture 均为合成样例）。"""
import pytest

from writersroom import project
from writersroom.core import WritersRoomError, Workspace, read_json, write_json


@pytest.fixture
def ws(tmp_path):
    return Workspace.init(tmp_path / "ws", title="合成样例项目")


def _add_normalized(ws: Workspace, source_id: str = "s1") -> None:
    """伪造 normalized/<source_id>.json（合成样例，结构同 spec §3）。"""
    write_json(ws.path("normalized", f"{source_id}.json"), {
        "schema": 1,
        "source": {"id": source_id, "original_path": f"sources/{source_id}/original.txt",
                   "format": "txt", "sha256": "0" * 64,
                   "imported_at": "2026-01-01T00:00:00Z", "tool": "writersroom 0.1.0"},
        "warnings": [],
        "blocks": [{"index": 0, "kind": "paragraph", "text": "合成样例文本",
                    "line_start": 1, "line_end": 1,
                    "scene_no": None, "episode": None, "page": None}],
        "text": "合成样例文本",
    })


class TestSetProfile:
    def test_updates_allowed_fields(self, ws):
        proj = project.set_profile(
            ws, title="夜航", format="剧集", genre=["悬疑", "家庭"],
            audience="成年观众", platform="流媒体", logline="一句合成故事梗概",
            goals=["验证故事核心"], constraints={"episodes": 12, "page_chars": 500},
            status="复盘",
        )
        assert proj["title"] == "夜航"
        assert proj["genre"] == ["悬疑", "家庭"]
        assert proj["constraints"] == {"episodes": 12, "page_chars": 500}
        assert proj["status"] == "复盘"
        on_disk = read_json(ws.path("project.json"))
        assert on_disk["title"] == "夜航"
        assert on_disk["goals"] == ["验证故事核心"]
        assert on_disk["logline"] == "一句合成故事梗概"

    def test_unknown_field_rejected(self, ws):
        with pytest.raises(WritersRoomError) as exc:
            project.set_profile(ws, budget="1亿")
        assert "budget" in exc.value.message
        assert "title" in exc.value.hint  # hint 列出允许字段

    def test_locks_not_settable_via_profile(self, ws):
        with pytest.raises(WritersRoomError) as exc:
            project.set_profile(ws, locks={"premise": True})
        assert "set_lock" in exc.value.hint

    @pytest.mark.parametrize("key,bad", [
        ("genre", "悬疑"), ("goals", "目标"), ("constraints", ["x"]), ("title", 42),
    ])
    def test_field_type_check(self, ws, key, bad):
        with pytest.raises(WritersRoomError) as exc:
            project.set_profile(ws, **{key: bad})
        assert key in exc.value.message


class TestVersions:
    def test_add_and_list(self, ws):
        _add_normalized(ws, "s1")
        _add_normalized(ws, "s2")
        e1 = project.version_add(ws, "s1", "v1", note="初稿")
        e2 = project.version_add(ws, "s2", "v2")
        assert e1["label"] == "v1" and e1["source_id"] == "s1"
        assert e1["note"] == "初稿" and e1["created_at"]
        assert e2["note"] == ""
        assert [v["label"] for v in project.version_list(ws)] == ["v1", "v2"]
        data = read_json(ws.path("versions.json"))
        assert data["schema"] == 1
        assert [v["source_id"] for v in data["versions"]] == ["s1", "s2"]

    def test_duplicate_label_rejected(self, ws):
        _add_normalized(ws)
        project.version_add(ws, "s1", "v1")
        with pytest.raises(WritersRoomError) as exc:
            project.version_add(ws, "s1", "v1")
        assert "v1" in exc.value.message
        assert "version list" in exc.value.hint

    def test_missing_normalized_rejected(self, ws):
        with pytest.raises(WritersRoomError) as exc:
            project.version_add(ws, "ghost", "v1")
        assert "ghost" in exc.value.message
        assert "ingest" in exc.value.hint

    @pytest.mark.parametrize("label", ["", "../escape", "a/b", "a\\b", ".hidden"])
    def test_invalid_label_rejected(self, ws, label):
        _add_normalized(ws)
        with pytest.raises(WritersRoomError):
            project.version_add(ws, "s1", label)

    def test_chinese_label_accepted(self, ws):
        _add_normalized(ws)
        entry = project.version_add(ws, "s1", "第3稿.修订")
        assert entry["label"] == "第3稿.修订"

    def test_list_empty(self, ws):
        assert project.version_list(ws) == []


class TestLocks:
    def test_set_and_unset(self, ws):
        proj = project.set_lock(ws, "premise")
        assert proj["locks"]["premise"] is True
        proj = project.set_lock(ws, "structure", "已确认三幕结构")
        assert proj["locks"]["structure"] == "已确认三幕结构"
        proj = project.unset_lock(ws, "premise")
        assert "premise" not in proj["locks"]
        assert read_json(ws.path("project.json"))["locks"] == {"structure": "已确认三幕结构"}

    def test_unset_absent_is_noop(self, ws):
        proj = project.unset_lock(ws, "causality")
        assert proj["locks"] == {}

    def test_unknown_key_rejected(self, ws):
        with pytest.raises(WritersRoomError) as exc:
            project.set_lock(ws, "budget")
        assert "premise" in exc.value.hint
        with pytest.raises(WritersRoomError):
            project.unset_lock(ws, "budget")
