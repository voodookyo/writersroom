"""stages.py 验收测试（spec §4.7）。"""
import json

import pytest

from writersroom import memory, project, stages
from writersroom.core import Workspace, WritersRoomError, read_json


@pytest.fixture
def ws(tmp_path):
    w = Workspace.init(tmp_path / "ws", title="合成测试剧")
    project.set_profile(w, logline="一个测试 logline", genre=["悬疑"], format="剧集")
    return w


def test_pipelines_load():
    pls = {p["id"]: p for p in stages.list_pipelines()}
    assert pls["new-project"]["stages"] == 9
    assert pls["in-dev"]["stages"] == 7
    assert pls["collab"]["stages"] == 20
    assert pls["webnovel"]["stages"] == 5


def test_run_produces_candidate(ws):
    run_dir = stages.run_stage(ws, "new-project", "materials")
    output = (run_dir / "output.md").read_text(encoding="utf-8")
    manifest = read_json(run_dir / "manifest.json")
    assert "候选产物" in output and "deterministic-template" in output
    assert "合成测试剧" in output and "一个测试 logline" in output
    assert manifest["layer"] == "deterministic-template"
    assert manifest["use_llm"] is False and manifest["input_hash"]
    # 未 confirm 前无 current.md
    assert not (ws.path("stages", "new-project", "materials", "current.md")).exists()


def test_confirm_creates_current_and_history(ws):
    run_dir = stages.run_stage(ws, "new-project", "materials")
    current = stages.confirm(ws, "new-project", "materials", run_dir.name)
    assert current.exists()
    hist = stages.history(ws, "new-project", "materials")
    assert [h["action"] for h in hist] == ["run", "confirm"]
    # confirm 后下一阶段的 prev 引用变化
    ctx = stages.build_context(ws, "new-project", "material-breakdown")
    assert ctx["prev.materials"] != "（前序阶段未确认）"


def test_confirmed_decisions_enter_context(ws):
    c = memory.add_candidate(ws, "decision", "主角设定为双人探案", source="测试")
    memory.decide(ws, c["id"], "confirm")
    ctx = stages.build_context(ws, "new-project", "materials")
    assert "双人探案" in ctx["decisions.confirmed"]


def test_locks_block_writing_stages(ws):
    with pytest.raises(WritersRoomError) as ei:
        stages.run_stage(ws, "collab", "scenes")
    assert "premise" in ei.value.message and ei.value.hint
    for key in project.LOCK_KEYS:
        project.set_lock(ws, key)
    run_dir = stages.run_stage(ws, "collab", "scenes")
    assert (run_dir / "output.md").exists()


def test_rerun_does_not_overwrite(ws):
    r1 = stages.run_stage(ws, "new-project", "materials")
    r2 = stages.run_stage(ws, "new-project", "materials")
    assert r1 != r2 and r1.exists() and r2.exists()
    body1 = (r1 / "output.md").read_text(encoding="utf-8").split("\n\n", 1)[1]
    body2 = (r2 / "output.md").read_text(encoding="utf-8").split("\n\n", 1)[1]
    assert body1 == body2  # 确定性：正文一致（头部 run_id 不同）
    assert read_json(r1 / "manifest.json")["input_hash"] == \
           read_json(r2 / "manifest.json")["input_hash"]


def test_webnovel_unstable_warning(ws):
    run_dir = stages.run_stage(ws, "webnovel", "market-watch")
    output = (run_dir / "output.md").read_text(encoding="utf-8")
    manifest = read_json(run_dir / "manifest.json")
    assert "不稳定依赖" in output and manifest["unstable_external"] is True
    assert any("不稳定依赖" in w for w in manifest["warnings"])


def test_use_llm_without_fn_errors(ws):
    with pytest.raises(WritersRoomError, match="未配置 LLM"):
        stages.run_stage(ws, "new-project", "materials", use_llm=True)


def test_llm_fn_failure_writes_nothing(ws):
    def boom(system, user):
        raise WritersRoomError("LLM 端点不可用: 连接拒绝", "提示")

    before = list(ws.path("stages").rglob("output.md"))
    with pytest.raises(WritersRoomError, match="连接拒绝"):
        stages.run_stage(ws, "new-project", "materials", use_llm=True, llm_fn=boom)
    after = list(ws.path("stages").rglob("output.md"))
    assert before == after  # 不伪造成功，不留半成品


def test_use_llm_marks_candidate_layer(ws):
    run_dir = stages.run_stage(ws, "new-project", "materials",
                               use_llm=True, llm_fn=lambda s, u: "# LLM 候选\n\n合成输出")
    output = (run_dir / "output.md").read_text(encoding="utf-8")
    assert "llm-candidate" in output and "合成输出" in output
    assert read_json(run_dir / "manifest.json")["layer"] == "llm-candidate"


def test_unknown_pipeline_and_stage(ws):
    with pytest.raises(WritersRoomError) as e1:
        stages.run_stage(ws, "nope", "materials")
    assert "可用管道" in (e1.value.hint or "")
    with pytest.raises(WritersRoomError) as e2:
        stages.run_stage(ws, "new-project", "nope")
    assert "可用阶段" in (e2.value.hint or "")


def test_confirm_unknown_run(ws):
    with pytest.raises(WritersRoomError) as ei:
        stages.confirm(ws, "new-project", "materials", "20990101T000000Z")
    assert "可用 run" in (ei.value.hint or "")
