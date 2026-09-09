"""CLI 全链路冒烟测试（spec §6/§7）：main() 直接调用，断言退出码与关键产物。"""
from pathlib import Path

import pytest

from writersroom.cli import main

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    assert main(["init", str(root), "--title", "烟测剧"]) == 0
    return root


def test_full_chain(ws, capsys):
    # ingest × 2 + version + analyze + compare
    assert main(["ingest", str(ws), str(SAMPLES / "mini.fountain"), "--source-id", "ep1v1"]) == 0
    assert main(["ingest", str(ws), str(SAMPLES / "mini_v2.fountain"), "--source-id", "ep1v2"]) == 0
    assert main(["version", "add", str(ws), "ep1v1", "v1"]) == 0
    assert main(["version", "add", str(ws), "ep1v2", "v2"]) == 0
    assert main(["analyze", str(ws), "v1"]) == 0
    assert main(["analyze", str(ws), "v2"]) == 0
    assert main(["compare", str(ws), "v1", "v2"]) == 0
    assert list(ws.glob("analysis/compare/v1__vs__v2/*/compare.md"))

    # memory → decision → tasks
    assert main(["memory", "add", str(ws), "--kind", "decision", "--text", "双人探案"]) == 0
    assert main(["decision", str(ws), "confirm", "CR0001"]) == 0
    assert main(["tasks", "gen", str(ws)]) == 0
    assert (ws / "tasks" / "tasks.md").exists()

    # meeting：四格式 normalize + compact + review + restore
    mid = "2026-08-20_剧本会"
    assert main(["meeting", "locate", str(ws), "--date", "2026-08-20", "--type", "剧本会"]) == 0
    for f in ("meeting_zoom.vtt", "meeting.srt", "meeting_iflytek.txt", "meeting_notes.txt"):
        assert main(["meeting", "ingest", str(ws), mid, str(SAMPLES / f)]) == 0
    assert main(["meeting", "normalize", str(ws), mid]) == 0
    assert main(["meeting", "compact", str(ws), mid]) == 0
    handoff = (ws / "meetings" / mid / "handoff.md").read_text(encoding="utf-8")
    assert "[?]" in handoff and "[决策]" in handoff  # UNKNOWN 与决策标记
    assert main(["meeting", "review", str(ws), mid, "rename-speaker",
                 "--old", "UNKNOWN", "--new", "记录员甲"]) == 0
    assert main(["meeting", "restore", str(ws), mid, "--seq", "0"]) == 0

    # stage：locks 拦截 → 锁定 → 放行 → confirm
    assert main(["stage", "run", str(ws), "collab", "scenes"]) == 2
    err = capsys.readouterr().err
    assert "解决：" in err
    for key in ("premise", "structure", "character_choice", "causality",
                "relationship_stage", "event_coverage", "episode_hook", "production_boundary"):
        assert main(["stage", "lock", str(ws), key]) == 0
    assert main(["stage", "run", str(ws), "collab", "scenes"]) == 0
    runs = sorted((ws / "stages" / "collab" / "scenes" / "runs").iterdir())
    assert main(["stage", "confirm", str(ws), "collab", "scenes", runs[0].name]) == 0
    assert (ws / "stages" / "collab" / "scenes" / "current.md").exists()
    assert main(["stage", "list"]) == 0
    assert main(["stage", "history", str(ws), "collab", "scenes"]) == 0

    # import / kb / scenes / docgen
    assert main(["import", "scan", str(ws), str(SAMPLES / "import_fixture")]) == 0
    assert main(["import", "apply", str(ws), str(SAMPLES / "import_fixture")]) == 0
    assert main(["kb", "search", str(ws), "节奏", "--dir", str(SAMPLES / "knowledge")]) == 0
    assert main(["scenes", "index", str(ws), "ep1v1"]) == 0
    out_docx = ws / "exports" / "烟测.docx"
    assert main(["docgen", str(SAMPLES / "docgen_spec.json"), str(out_docx)]) == 0
    assert out_docx.exists()

    # llm 未配置（合法状态，exit 0）
    assert main(["llm", "check"]) == 0


def test_error_exit_code_and_hint(tmp_path, capsys):
    missing = tmp_path / "nowhere"
    assert main(["analyze", str(missing), "v1"]) == 2
    err = capsys.readouterr().err
    assert "错误：" in err and "解决：" in err


def test_kb_no_hit_is_explicit(ws, capsys):
    # 词表外查询（任何 token 都不在语料中）必须明确「未命中」而非伪造结果
    assert main(["kb", "search", str(ws), "zxqwv", "asdfq", "--dir",
                 str(SAMPLES / "knowledge")]) == 0
    out = capsys.readouterr().out
    assert "未命中" in out and "已索引" in out


def test_ingest_unknown_file(ws, capsys):
    assert main(["ingest", str(ws), str(SAMPLES / "nope.xyz")]) == 2
    assert "解决：" in capsys.readouterr().err
