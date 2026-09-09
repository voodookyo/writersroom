"""hints 模块测试：八维结构、disclaimer、阈值驱动的评分行为。"""
from __future__ import annotations

from pathlib import Path

import pytest

from writersroom import analyze, hints, ingest
from writersroom.core import Workspace

SAMPLE = Path(__file__).resolve().parents[1] / "samples" / "mini.fountain"

DIM_IDS = {"story_core", "genre_fit", "character_drive", "relationship_tension",
           "pace_hooks", "emotional_value", "market_identity",
           "production_feasibility"}


def make_doc(text: str) -> dict:
    return {
        "schema": 1,
        "source": {},
        "warnings": [],
        "blocks": [{"index": 0, "kind": "action", "text": text,
                    "line_start": 1, "line_end": 1,
                    "scene_no": None, "episode": None, "page": None}],
        "text": text,
    }


def build_for_text(text: str, project: dict | None = None) -> dict:
    doc = make_doc(text)
    stats = analyze.analyze_document(doc)
    signals = analyze.compute_signals(doc)
    return hints.build_hints(doc, stats, signals, project or {})


@pytest.fixture()
def sample_hints(tmp_path):
    ws = Workspace.init(tmp_path / "ws", title="合成测试项目")
    _, doc = ingest.ingest(ws, SAMPLE)
    stats = analyze.analyze_document(doc, ws)
    signals = analyze.compute_signals(doc, ws)
    return hints.build_hints(doc, stats, signals, ws.project())


def test_eight_dimensions_complete(sample_hints):
    assert sample_hints["disclaimer"] == "启发式开发提示，非艺术结论或平台认证"
    assert sample_hints["produced_by"].startswith("writersroom")
    assert set(sample_hints["dimensions"]) == DIM_IDS
    for dim in sample_hints["dimensions"].values():
        assert set(dim) >= {"score", "evidence", "reasoning", "risks",
                            "priority", "suggestions"}
        assert isinstance(dim["reasoning"], list) and dim["reasoning"]
        assert all(isinstance(r, str) and r for r in dim["reasoning"])
        assert isinstance(dim["risks"], list)
        assert isinstance(dim["suggestions"], list)
        assert isinstance(dim["evidence"], list)


def test_score_range_invariant(sample_hints):
    for dim in sample_hints["dimensions"].values():
        assert 0 <= dim["score"] <= 5
        assert dim["priority"] in ("high", "medium", "low")
    # 低分维度应映射为 high 优先级（阈值表 priority.high_max=2）
    for dim in sample_hints["dimensions"].values():
        if dim["score"] <= 2:
            assert dim["priority"] == "high"


def test_thresholds_drive_story_core():
    rich = build_for_text("两人对峙，互相威胁，最终决裂，冲突爆发。")
    plain = build_for_text("今天天气不错，大家各自散步。")
    a = rich["dimensions"]["story_core"]["score"]
    b = plain["dimensions"]["story_core"]["score"]
    assert a > b
    assert plain["dimensions"]["story_core"]["risks"]  # 弱冲突规则给出风险


def test_high_cost_penalizes_feasibility():
    costly = build_for_text("爆破组引爆，航拍追车，水下枪战，群演封路。")
    cheap = build_for_text("两人坐在屋里聊天。")
    a = costly["dimensions"]["production_feasibility"]["score"]
    b = cheap["dimensions"]["production_feasibility"]["score"]
    assert a < b
    assert costly["dimensions"]["production_feasibility"]["risks"]


def test_genre_and_logline_registration():
    project = {"genre": ["悬疑"], "logline": "一封信揭开的家族秘密。"}
    h = build_for_text("两人对峙，互相威胁，真相揭晓。", project)
    assert h["dimensions"]["genre_fit"]["score"] >= 3
    assert h["dimensions"]["market_identity"]["score"] >= 3


def test_reasoning_templates_are_chinese(sample_hints):
    for dim in sample_hints["dimensions"].values():
        for r in dim["reasoning"]:
            assert any("一" <= ch <= "鿿" for ch in r)
