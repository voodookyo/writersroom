"""compare 模块测试（fixture 均为合成样例）。

与模块 A（analyze）解耦：直接伪造 versions.json + analysis/<label>/<run_id>/{stats,hints}.json，
并用假 writersroom.analyze 模块注入 sys.modules 替代真实 latest_run/run_analysis。
"""
import sys
import types

import pytest

from writersroom import compare
from writersroom.core import WritersRoomError, Workspace, read_json, write_json

RUN_A = "20260101T000000Z"
RUN_B = "20260102T000000Z"

_STATS_META = {"schema": 1, "produced_by": "writersroom 0.1.0",
               "created_at": "2026-01-01T00:00:00Z", "source_hash": "0" * 64}

STATS_A = {
    **_STATS_META,
    "chars_total": 1000, "chars_no_space": 900, "paragraphs": 20, "sentences": 30,
    "scenes": 4, "episodes": 1, "dialogue_lines": 8, "dialogue_chars": 2,
    "est_pages": 2.0, "dialogue_by_character": {"林夏": 5, "陈默": 3},
    "keywords": {"method": "tfidf(char_ngram)",
                 "items": [{"term": "雨夜", "score": 0.9}, {"term": "秘密", "score": 0.8}]},
    "reading_minutes": 3,
}
STATS_B = {
    **_STATS_META,
    "chars_total": 1250, "chars_no_space": 1125, "paragraphs": 24, "sentences": 36,
    "scenes": 5, "episodes": 2, "dialogue_lines": 9, "dialogue_chars": 2,
    "est_pages": 2.5, "dialogue_by_character": {"林夏": 7, "顾远": 2},
    "keywords": {"method": "tfidf(char_ngram)",
                 "items": [{"term": "雨夜", "score": 0.9}, {"term": "真相", "score": 0.7}]},
    "reading_minutes": 4,
}


def _hints(dimensions: dict) -> dict:
    """dimensions: {dim_id: (score, [risk, …])}；结构同 spec §4.3。"""
    return {
        "schema": 1, "produced_by": "writersroom 0.1.0",
        "created_at": "2026-01-01T00:00:00Z", "source_hash": "0" * 64,
        "disclaimer": "启发式开发提示，非艺术结论或平台认证",
        "dimensions": [
            {"id": d, "score": sc, "evidence": [], "reasoning": "合成样例",
             "risks": list(risks), "priority": "medium", "suggestions": []}
            for d, (sc, risks) in dimensions.items()
        ],
    }


HINTS_A = _hints({
    "story_core": (2, ["主目标模糊", "主题表达直白"]),
    "genre_fit": (3, ["类型元素不足"]),
    "pace_hooks": (3, ["集尾无钩子"]),
})
HINTS_B = _hints({
    "story_core": (3, ["主目标模糊"]),          # 分数升 → 共有 risk 归 improved；另一 risk 消失 → removed
    "genre_fit": (3, ["类型元素不足"]),          # 分数不变 → persistent
    "pace_hooks": (2, ["集尾无钩子"]),           # 分数降 → regressed
    "character_drive": (2, ["人物动机缺失"]),    # 仅 b 有 → new
})


def _version(ws: Workspace, label: str, source_id: str) -> None:
    p = ws.path("versions.json")
    data = read_json(p)
    data["versions"].append({"label": label, "source_id": source_id,
                             "note": "", "created_at": "2026-01-01T00:00:00Z"})
    write_json(p, data)


def _run(ws: Workspace, label: str, run_id: str, stats: dict, hints: dict):
    d = ws.path("analysis", label, run_id)
    write_json(d / "stats.json", stats)
    write_json(d / "hints.json", hints)
    return d


@pytest.fixture(autouse=True)
def fake_analyze(monkeypatch):
    """假 writersroom.analyze：latest_run 扫描 analysis/<label>/ 取目录名最大者。"""
    mod = types.ModuleType("writersroom.analyze")
    state = {"run_calls": []}

    def latest_run(ws, label):
        base = ws.path("analysis", label)
        if not base.is_dir():
            return None
        runs = sorted(p.name for p in base.iterdir() if p.is_dir())
        return base / runs[-1] if runs else None

    def run_analysis(ws, label):  # 默认不应被调用；需要时各测试自行替换
        state["run_calls"].append(label)
        raise WritersRoomError("测试环境不提供真实分析", "在测试里伪造 analysis run")

    mod.latest_run = latest_run
    mod.run_analysis = run_analysis
    monkeypatch.setitem(sys.modules, "writersroom.analyze", mod)
    return state


def _make_ws(tmp_path, with_b_run: bool = True) -> Workspace:
    ws = Workspace.init(tmp_path / "ws")
    _version(ws, "v1", "s1")
    _version(ws, "v2", "s2")
    _run(ws, "v1", RUN_A, STATS_A, HINTS_A)
    if with_b_run:
        _run(ws, "v2", RUN_B, STATS_B, HINTS_B)
    return ws


class TestStatAndChanges:
    def test_stat_diff(self, tmp_path):
        result = compare.compare(_make_ws(tmp_path), "v1", "v2")
        assert result["schema"] == 1
        assert result["produced_by"].startswith("writersroom")
        assert result["created_at"] and result["source_hash"]
        assert result["a"] == {"label": "v1", "source_id": "s1", "run_id": RUN_A}
        assert result["b"] == {"label": "v2", "source_id": "s2", "run_id": RUN_B}
        sd = result["stat_diff"]
        assert list(sd) == list(compare.STAT_KEYS)
        assert sd["chars_total"] == {"a": 1000, "b": 1250, "delta": 250}
        assert sd["est_pages"] == {"a": 2.0, "b": 2.5, "delta": 0.5}
        assert sd["episodes"] == {"a": 1, "b": 2, "delta": 1}

    def test_character_changes(self, tmp_path):
        cc = compare.compare(_make_ws(tmp_path), "v1", "v2")["character_changes"]
        assert cc["added"] == ["顾远"]
        assert cc["removed"] == ["陈默"]
        assert cc["frequency_delta"] == {"林夏": {"a": 5, "b": 7, "delta": 2}}

    def test_keyword_changes(self, tmp_path):
        kc = compare.compare(_make_ws(tmp_path), "v1", "v2")["keyword_changes"]
        assert kc["added"] == ["真相"]
        assert kc["removed"] == ["秘密"]


class TestDimensionsAndIssues:
    def test_dimension_diff(self, tmp_path):
        diff = compare.compare(_make_ws(tmp_path), "v1", "v2")["dimension_diff"]
        assert [d["id"] for d in diff] == list(compare.DIMENSION_IDS)
        by_id = {d["id"]: d for d in diff}
        assert by_id["story_core"] == {"id": "story_core", "a": 2, "b": 3, "delta": 1}
        assert by_id["pace_hooks"]["delta"] == -1
        assert by_id["genre_fit"]["delta"] == 0
        assert by_id["character_drive"] == {"id": "character_drive", "a": None, "b": 2, "delta": None}
        assert by_id["market_identity"]["delta"] is None

    def test_issues_five_categories(self, tmp_path):
        issues = compare.compare(_make_ws(tmp_path), "v1", "v2")["issues"]

        def risks(cat):
            return [i["risk"] for i in issues[cat]]

        assert risks("new") == ["人物动机缺失"]
        assert risks("removed") == ["主题表达直白"]
        assert risks("improved") == ["主目标模糊"]
        assert risks("regressed") == ["集尾无钩子"]
        assert risks("persistent") == ["类型元素不足"]
        imp = issues["improved"][0]
        assert imp["id"].startswith("story_core:")
        assert imp["dimension"] == "story_core"
        assert (imp["score_a"], imp["score_b"], imp["score_delta"]) == (2, 3, 1)
        # 五分类互斥且并集 = 两版全部 issue
        all_ids = [i["id"] for cat in issues.values() for i in cat]
        assert len(all_ids) == len(set(all_ids)) == 5


class TestOutput:
    def test_compare_files_written(self, tmp_path):
        ws = _make_ws(tmp_path)
        result = compare.compare(ws, "v1", "v2")
        base = ws.path("analysis", "compare", "v1__vs__v2")
        runs = [p for p in base.iterdir() if p.is_dir()]
        assert len(runs) == 1
        assert read_json(runs[0] / "compare.json") == result

    def test_compare_md_content(self, tmp_path):
        ws = _make_ws(tmp_path)
        compare.compare(ws, "v1", "v2")
        base = ws.path("analysis", "compare", "v1__vs__v2")
        md = next(base.iterdir()).joinpath("compare.md").read_text(encoding="utf-8")
        assert md.startswith("# 版本比较：v1 → v2")
        assert "| chars_total | 1000 | 1250 | +250 |" in md
        assert "story_core（故事核心）" in md
        assert "新增问题" in md and "持续存在的问题" in md
        assert "主目标模糊" in md and "人物动机缺失" in md

    def test_deterministic_except_timestamp(self, tmp_path):
        ws = _make_ws(tmp_path)
        r1 = compare.compare(ws, "v1", "v2")
        r2 = compare.compare(ws, "v1", "v2")
        strip = lambda r: {k: v for k, v in r.items() if k != "created_at"}
        assert strip(r1) == strip(r2)


class TestErrors:
    def test_unknown_label(self, tmp_path):
        ws = _make_ws(tmp_path)
        with pytest.raises(WritersRoomError) as exc:
            compare.compare(ws, "v1", "v9")
        assert "v9" in exc.value.message
        assert "version list" in exc.value.hint

    def test_same_label_rejected(self, tmp_path):
        with pytest.raises(WritersRoomError) as exc:
            compare.compare(_make_ws(tmp_path), "v1", "v1")
        assert "相同" in exc.value.message


class TestAnalyzeIntegration:
    def test_missing_run_triggers_run_analysis(self, tmp_path, fake_analyze, monkeypatch):
        """v2 无 analysis run → compare 自动调用 analyze.run_analysis 补齐。"""
        ws = _make_ws(tmp_path, with_b_run=False)
        mod = sys.modules["writersroom.analyze"]

        def run_analysis(ws2, label):
            fake_analyze["run_calls"].append(label)
            return _run(ws2, label, RUN_B, STATS_B, HINTS_B)

        monkeypatch.setattr(mod, "run_analysis", run_analysis)
        result = compare.compare(ws, "v1", "v2")
        assert fake_analyze["run_calls"] == ["v2"]  # 只补 v2，v1 已有 run 不重复跑
        assert result["stat_diff"]["chars_total"] == {"a": 1000, "b": 1250, "delta": 250}
        assert result["b"]["run_id"] == RUN_B

    def test_run_analysis_failure_surfaces(self, tmp_path, fake_analyze):
        """自动补齐失败（默认假实现报错）→ 错误原样抛出，不伪造成功。"""
        ws = _make_ws(tmp_path, with_b_run=False)
        with pytest.raises(WritersRoomError, match="测试环境不提供真实分析"):
            compare.compare(ws, "v1", "v2")
        assert fake_analyze["run_calls"] == ["v2"]

    def test_latest_run_selected(self, tmp_path):
        """同版本多个 run 时取最新（目录名最大者）。"""
        ws = _make_ws(tmp_path)
        _run(ws, "v1", "20260103T000000Z", dict(STATS_A, chars_total=1100), HINTS_A)
        result = compare.compare(ws, "v1", "v2")
        assert result["a"]["run_id"] == "20260103T000000Z"
        assert result["stat_diff"]["chars_total"]["a"] == 1100
