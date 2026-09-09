"""importer 测试：dry-run 无副作用、EP01 规范化、frontmatter 优先、skip_exists、conflict。"""
import shutil
from pathlib import Path

import pytest

from writersroom import importer
from writersroom.core import Workspace, WritersRoomError, read_json

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
FIXTURE = SAMPLES / "import_fixture"


def make_ws(tmp_path: Path) -> Workspace:
    return Workspace.init(tmp_path / "ws")


def test_scan_plan_classification_and_normalization(tmp_path):
    ws = make_ws(tmp_path)
    plan = importer.scan(ws, FIXTURE)
    by_file = {e["file"]: e for e in plan["files"]}

    # 集数前缀规范化（保留原扩展名）
    assert by_file["第1集剧本.txt"]["target_name"] == "剧本/EP01剧本.txt"
    assert by_file["e02_剧本.txt"]["target_name"] == "剧本/EP02_剧本.txt"
    assert by_file["第3集大纲.txt"]["target_name"] == "大纲/EP03大纲.txt"
    assert importer.normalize_episode_prefix("第01集.txt") == "EP01.txt"
    assert importer.normalize_episode_prefix("E1.txt") == "EP01.txt"

    # 文件名规则分类
    assert by_file["会议纪要-20260801.txt"]["category"] == "会议"
    assert by_file["参考-类型片.txt"]["category"] == "参考"
    assert by_file["角色小传-林晓.txt"]["category"] == "素材"  # 兜底

    # md frontmatter category 优先于文件名规则/兜底
    assert by_file["剧本围读笔记.md"]["category"] == "会议"
    assert by_file["剧本围读笔记.md"]["matched_by"] == "frontmatter"
    assert by_file["导演阐述.md"]["category"] == "参考"
    assert by_file["导演阐述.md"]["matched_by"] == "frontmatter"

    # 目标均不存在 → 全部 copy
    assert all(e["action"] == "copy" for e in plan["files"])
    assert plan["summary"] == {"total": 8, "copy": 8, "skip_exists": 0, "conflict": 0}


def test_scan_dry_run_has_no_side_effects(tmp_path):
    ws = make_ws(tmp_path)
    plan = importer.scan(ws, FIXTURE)
    # 只写 imports/<run_id>/plan.json
    assert (ws.path("imports", plan["run_id"], "plan.json")).exists()
    written = read_json(ws.path("imports", plan["run_id"], "plan.json"))
    assert written["files"] == plan["files"]
    # 不产生分类目录、不复制文件、不写 report.md
    for cat in importer.CATEGORIES:
        assert not ws.path(cat).exists()
    assert not (ws.path("imports", plan["run_id"], "report.md")).exists()


def test_scan_deterministic_except_run_id_and_time(tmp_path):
    ws = make_ws(tmp_path)
    p1 = importer.scan(ws, FIXTURE)
    p2 = importer.scan(ws, FIXTURE)
    for p in (p1, p2):
        p.pop("run_id")
        p.pop("created_at")
    assert p1 == p2
    # 同秒双跑 run_id 不冲突
    ws2 = make_ws(tmp_path / "second")
    a = importer.scan(ws2, FIXTURE)["run_id"]
    b = importer.scan(ws2, FIXTURE)["run_id"]
    assert a != b


def test_apply_copy_then_skip_exists(tmp_path):
    ws = make_ws(tmp_path)
    report = importer.apply(ws, FIXTURE)
    assert report["summary"]["copied"] == 8
    assert ws.path("剧本", "EP01剧本.txt").exists()
    assert ws.path("会议", "剧本围读笔记.md").exists()
    assert ws.path("素材", "角色小传-林晓.txt").exists()
    # 复制内容一致
    assert (ws.path("剧本", "EP01剧本.txt")).read_bytes() == (FIXTURE / "第1集剧本.txt").read_bytes()
    report_md = ws.path("imports", report["run_id"], "report.md")
    assert report_md.exists()
    text = report_md.read_text(encoding="utf-8")
    assert "成功复制（8）" in text and "跳过：目标已存在（0）" in text

    # 再次 apply：全部 skip_exists，绝不覆盖
    before = (ws.path("剧本", "EP01剧本.txt")).read_bytes()
    report2 = importer.apply(ws, FIXTURE)
    assert report2["summary"]["copied"] == 0
    assert report2["summary"]["skip_exists"] == 8
    assert (ws.path("剧本", "EP01剧本.txt")).read_bytes() == before


def test_conflict_same_target(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "第1集_剧本.txt").write_text("合成样例 A", encoding="utf-8")
    (src / "e01_剧本.txt").write_text("合成样例 B", encoding="utf-8")  # 与上一个同目标 EP01_剧本.txt
    ws = make_ws(tmp_path)
    plan = importer.scan(ws, src)
    actions = {e["file"]: e["action"] for e in plan["files"]}
    assert sorted(actions.values()) == ["conflict", "copy"]
    report = importer.apply(ws, src)
    assert report["summary"]["copied"] == 1
    assert report["summary"]["conflict"] == 1
    assert ws.path("剧本", "EP01_剧本.txt").exists()


def test_scan_missing_dir_errors(tmp_path):
    ws = make_ws(tmp_path)
    with pytest.raises(WritersRoomError) as exc:
        importer.scan(ws, tmp_path / "不存在")
    assert exc.value.hint  # hint 必须可操作
