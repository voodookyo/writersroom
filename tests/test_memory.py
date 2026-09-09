"""memory 模块测试：候选/决定 append-only、五状态映射、任务生成三来源。

所有数据均为合成样例，非真实项目资料。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from writersroom import memory
from writersroom.core import (
    WritersRoomError, Workspace, append_jsonl, read_json, read_jsonl, write_json,
)


@pytest.fixture
def ws(tmp_path):
    return Workspace.init(tmp_path / "ws", title="合成测试项目")


def _old_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _park_decision_row(dec_id: str, target_id: str, created_at: str) -> dict:
    return {
        "id": dec_id,
        "target_id": target_id,
        "action": "park",
        "text": "",
        "actor": "human",
        "created_at": created_at,
    }


def _write_hints(ws, version: str, run_id: str, dimensions: dict) -> None:
    write_json(ws.path("analysis", version, run_id, "hints.json"), {
        "schema": 1,
        "disclaimer": "启发式开发提示，非艺术结论或平台认证",
        "dimensions": dimensions,
    })


# ---------- add_candidate ----------

def test_add_candidate_increments_and_appends(ws):
    r1 = memory.add_candidate(ws, "note", "主角动机需要再确认", "meeting 合成晨会")
    r2 = memory.add_candidate(ws, "fact", "集数上限 12 集", "")
    assert r1["id"] == "CR0001"
    assert r2["id"] == "CR0002"
    assert r1["status"] == "candidate"
    assert r2["source"] == "manual"  # 空 source 记为 manual
    rows = read_jsonl(ws.path("memory", "candidates.jsonl"))
    assert [r["id"] for r in rows] == ["CR0001", "CR0002"]
    # 追加而非覆盖：第二行的写入不改变第一行内容
    assert rows[0]["text"] == "主角动机需要再确认"
    assert rows[0]["kind"] == "note"


def test_add_candidate_rejects_bad_kind_and_empty_text(ws):
    with pytest.raises(WritersRoomError) as e:
        memory.add_candidate(ws, "wish", "x", "y")
    assert e.value.hint  # hint 必须可操作
    with pytest.raises(WritersRoomError):
        memory.add_candidate(ws, "note", "   ", "y")


def test_add_candidate_requires_workspace(tmp_path):
    ws = Workspace(tmp_path / "nowhere")
    with pytest.raises(WritersRoomError):
        memory.add_candidate(ws, "note", "x", "y")


# ---------- decide / effective_status ----------

def test_decide_requires_existing_target(ws):
    with pytest.raises(WritersRoomError) as e:
        memory.decide(ws, "CR9999", "confirm")
    assert "CR9999" in e.value.message
    assert e.value.hint


def test_decide_rejects_unknown_action(ws):
    c = memory.add_candidate(ws, "note", "候选甲", "manual")
    with pytest.raises(WritersRoomError):
        memory.decide(ws, c["id"], "maybe")


def test_five_status_mapping(ws):
    expected = {
        "confirm": "已确认",
        "propose": "提案",
        "reject": "否决",
        "park": "暂存",
        "question": "待核问题",
    }
    ids = {}
    for action in expected:
        c = memory.add_candidate(ws, "note", f"候选-{action}", "manual")
        memory.decide(ws, c["id"], action)
        ids[c["id"]] = action
    plain = memory.add_candidate(ws, "note", "无决定候选", "manual")

    statuses = memory.effective_status(ws)
    for cid, action in ids.items():
        assert statuses[cid] == expected[action]
    assert statuses[plain["id"]] == "候选"


def test_effective_status_latest_decision_wins(ws):
    c = memory.add_candidate(ws, "decision", "结局采用双时空结构", "manual")
    memory.decide(ws, c["id"], "propose", text="先记着")
    memory.decide(ws, c["id"], "question", text="观众是否看得懂？")
    memory.decide(ws, c["id"], "confirm", text="会上确认可行")
    assert memory.effective_status(ws)[c["id"]] == "已确认"
    # 再写一条新决定即可改判，旧记录不动
    memory.decide(ws, c["id"], "park", text="暂缓")
    assert memory.effective_status(ws)[c["id"]] == "暂存"


def test_decisions_append_only(ws):
    c = memory.add_candidate(ws, "note", "候选乙", "manual")
    d1 = memory.decide(ws, c["id"], "propose")
    memory.decide(ws, c["id"], "park")
    d3 = memory.decide(ws, c["id"], "confirm")
    rows = read_jsonl(ws.path("memory", "decisions.jsonl"))
    assert [r["id"] for r in rows] == ["DC0001", "DC0002", "DC0003"]
    # 追加而非覆盖：首行仍是第一条决定的原文
    assert rows[0] == d1
    assert rows[2]["id"] == d3["id"]
    # candidates.jsonl 原始行不受决定影响
    cands = read_jsonl(ws.path("memory", "candidates.jsonl"))
    assert len(cands) == 1 and cands[0]["status"] == "candidate"


# ---------- list_memory ----------

def test_list_memory_joins_latest_decision_and_filters(ws):
    a = memory.add_candidate(ws, "note", "候选A", "manual")
    b = memory.add_candidate(ws, "fact", "候选B", "manual")
    memory.decide(ws, a["id"], "question", text="待核实", actor="编剧甲")
    memory.decide(ws, b["id"], "confirm")

    all_items = memory.list_memory(ws)
    assert [i["id"] for i in all_items] == [a["id"], b["id"]]
    item_a = all_items[0]
    assert item_a["effective_status"] == "待核问题"
    assert item_a["latest_decision"]["actor"] == "编剧甲"
    assert item_a["latest_decision"]["text"] == "待核实"
    assert all_items[1]["effective_status"] == "已确认"

    questions = memory.list_memory(ws, status="待核问题")
    assert [i["id"] for i in questions] == [a["id"]]
    assert memory.list_memory(ws, status="否决") == []
    with pytest.raises(WritersRoomError):
        memory.list_memory(ws, status="随便")


# ---------- gen_tasks ----------

def test_gen_tasks_questions_and_no_analysis_message(ws):
    q = memory.add_candidate(ws, "note", "主角第二集动机存疑", "manual")
    ok = memory.add_candidate(ws, "note", "类型定位为悬疑", "manual")
    memory.decide(ws, q["id"], "question")
    memory.decide(ws, ok["id"], "confirm")

    doc = memory.gen_tasks(ws)
    assert doc["schema"] == 1
    assert doc["produced_by"].startswith("writersroom")
    assert doc["created_at"] and doc["generated_at"] and doc["source_hash"]
    assert "未找到" in doc["message"] and "analysis" in doc["message"]

    assert len(doc["tasks"]) == 1
    t = doc["tasks"][0]
    assert t["id"] == "T0001"
    assert t["title"] == "[待核] 主角第二集动机存疑"
    assert t["status"] == "open"
    assert t["source_ref"] == f"memory/candidates.jsonl#{q['id']}"
    assert t["why"]


def test_gen_tasks_high_risks_uses_latest_run_only(ws):
    # 旧 run 的风险不应出现，只取最新 run
    _write_hints(ws, "v1", "20260801T000000Z", {
        "story_core": {"priority": "high", "risks": ["旧风险不应出现"]},
    })
    _write_hints(ws, "v1", "20260820T000000Z", {
        "story_core": {"priority": "high", "risks": ["核心冲突不清晰", "目标受众模糊"]},
        "pace_hooks": {"priority": "low", "risks": ["低优先级不应出现"]},
        "genre_fit": {"priority": "high", "risks": []},
    })

    doc = memory.gen_tasks(ws)
    titles = [t["title"] for t in doc["tasks"]]
    assert doc["message"] == ""
    assert "[高风险] story_core：核心冲突不清晰" in titles
    assert "[高风险] story_core：目标受众模糊" in titles
    assert not any("旧风险" in t or "低优先级" in t for t in titles)
    for t in doc["tasks"]:
        assert t["source_ref"].startswith("analysis/v1/20260820T000000Z/hints.json#")


def test_gen_tasks_parked_overdue(ws):
    old = memory.add_candidate(ws, "note", "旧暂存项", "manual")
    fresh = memory.add_candidate(ws, "note", "新暂存项", "manual")
    # 模拟历史：old 在 30 天前被暂存，fresh 刚刚被暂存
    append_jsonl(ws.path("memory", "decisions.jsonl"),
                 _park_decision_row("DC0001", old["id"], _old_iso(30)))
    memory.decide(ws, fresh["id"], "park")

    doc = memory.gen_tasks(ws, parked_days=14)
    titles = [t["title"] for t in doc["tasks"]]
    assert "[暂存超期] 旧暂存项" in titles
    assert "[暂存超期] 新暂存项" not in titles

    # 阈值放宽到 31 天后旧项也不再超期
    doc2 = memory.gen_tasks(ws, parked_days=31)
    assert not any(t["title"].startswith("[暂存超期]") for t in doc2["tasks"])
    with pytest.raises(WritersRoomError):
        memory.gen_tasks(ws, parked_days=-1)


def test_gen_tasks_idempotent_and_title_dedup(ws):
    memory.add_candidate(ws, "note", "重复标题候选", "manual")
    memory.add_candidate(ws, "note", "重复标题候选", "manual")
    rows = read_jsonl(ws.path("memory", "candidates.jsonl"))
    for r in rows:
        memory.decide(ws, r["id"], "question")

    doc1 = memory.gen_tasks(ws)
    doc2 = memory.gen_tasks(ws)
    assert doc1["tasks"] == doc2["tasks"]
    assert doc1["source_hash"] == doc2["source_hash"]
    assert len(doc1["tasks"]) == 1  # 同 title 去重

    on_disk = read_json(ws.path("tasks", "tasks.json"))
    assert on_disk["tasks"] == doc1["tasks"]
    # 重跑是全量覆盖：行数不翻倍
    assert len(on_disk["tasks"]) == 1


def test_gen_tasks_writes_tasks_md(ws):
    q = memory.add_candidate(ws, "note", "钩子是否足够强", "manual")
    memory.decide(ws, q["id"], "question")
    _write_hints(ws, "v1", "20260820T000000Z", {
        "production_feasibility": {"priority": "high", "risks": ["雨夜车戏成本超标"]},
    })

    doc = memory.gen_tasks(ws)
    md = ws.path("tasks", "tasks.md").read_text(encoding="utf-8")
    assert "# 任务建议清单" in md
    assert "## 待核问题（1）" in md
    assert "## 高优先级风险（1）" in md
    assert "## 暂存超期（0）" in md
    assert "（无）" in md
    for t in doc["tasks"]:
        assert f"- [ ] {t['id']} {t['title']}" in md
        assert t["source_ref"] in md
    assert "不会自动执行" in md


def test_gen_tasks_corrupt_hints_is_actionable_error(ws):
    bad = ws.path("analysis", "v1", "20260820T000000Z", "hints.json")
    bad.parent.mkdir(parents=True)
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(WritersRoomError) as e:
        memory.gen_tasks(ws)
    assert "hints.json" in e.value.message
    assert e.value.hint


def test_gen_tasks_accepts_dimensions_list_layout(ws):
    # hints.json 的 dimensions 为列表布局时也能解析
    write_json(ws.path("analysis", "v1", "20260820T000000Z", "hints.json"), {
        "schema": 1,
        "disclaimer": "启发式开发提示，非艺术结论或平台认证",
        "dimensions": [
            {"dimension": "market_identity", "priority": "high", "risks": ["辨识度不足"]},
        ],
    })
    doc = memory.gen_tasks(ws)
    assert [t["title"] for t in doc["tasks"]] == ["[高风险] market_identity：辨识度不足"]


def test_tasks_json_is_sorted_keys_utf8(ws):
    c = memory.add_candidate(ws, "note", "含中文的任务来源", "manual")
    memory.decide(ws, c["id"], "question")
    memory.gen_tasks(ws)
    raw = ws.path("tasks", "tasks.json").read_text(encoding="utf-8")
    assert "含中文" in raw  # ensure_ascii=False
    assert raw.endswith("\n")
    parsed = json.loads(raw)
    assert list(parsed) == sorted(parsed)  # write_json 排序键
