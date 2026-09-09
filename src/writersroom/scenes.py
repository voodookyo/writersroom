"""场次索引：从 normalized 文档模型提取场景导航（docs/design/spec.md §4.8）。

- 场 = scene_heading 块到下一场景头之间的全部块；集/章 heading 块同样是场边界
  （集标题不属于任何场，否则其文本会混入上一场的收尾摘录）；
- 对白角色 = 场内 character 块按首次出现去重；
- opening/closing = 场内首/末非空块的原文摘录（≤50 字，截断加 …），只摘录不改写；
- 无场景 → 返回空清单 + message，不伪造。
"""
from __future__ import annotations

from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, new_run_id, now_iso,
    read_json, sha256_file, write_json,
)

EXCERPT_MAX = 50
_SCENE_BOUNDARY = ("scene_heading", "heading")


def _excerpt(text: str) -> str:
    text = text.strip()
    return text if len(text) <= EXCERPT_MAX else text[:EXCERPT_MAX] + "…"


def build_index(doc: dict) -> dict:
    """从 normalized 文档模型提取 {scene_count, scenes}；无场景带 message。"""
    blocks = doc.get("blocks") or []
    scenes: list[dict] = []
    head: dict | None = None      # 当前场的 scene_heading 块
    content: list[dict] = []      # 当前场的内容块

    def flush() -> None:
        nonlocal head, content
        if head is None:
            return
        characters: list[str] = []
        for b in content:
            if b.get("kind") == "character":
                name = b.get("text", "").strip()
                if name and name not in characters:
                    characters.append(name)
        nonempty = [b for b in content if b.get("text", "").strip()]
        last = content[-1] if content else head
        scenes.append({
            "scene_no": str(head.get("scene_no") or len(scenes) + 1),
            "heading": head.get("text", ""),
            "source_location": {
                "line_start": head.get("line_start"),
                "line_end": last.get("line_end"),
                "page": head.get("page"),
            },
            "dialogue_characters": characters,
            "opening_excerpt": _excerpt(nonempty[0]["text"]) if nonempty else "",
            "closing_excerpt": _excerpt(nonempty[-1]["text"]) if nonempty else "",
        })
        head, content = None, []

    for b in blocks:
        kind = b.get("kind")
        if kind in _SCENE_BOUNDARY:
            flush()
            if kind == "scene_heading":
                head = b
        elif head is not None:
            content.append(b)
    flush()

    result: dict = {"scene_count": len(scenes), "scenes": scenes}
    if not scenes:
        result["message"] = "未检测到场景（文档中没有 scene_heading 块）"
    return result


def _render_scenes_md(payload: dict) -> str:
    lines = [
        f"# 场次索引：{payload['source_id']}",
        "",
        f"- run_id：`{payload['run_id']}` ｜ 生成：{payload['created_at']}（{payload['produced_by']} 自动产物）",
        f"- 场景数：{payload['scene_count']}",
        "",
    ]
    if payload["scene_count"] == 0:
        lines.append(f"> {payload.get('message', '未检测到场景')}")
    else:
        lines += [
            "| 场号 | 场景头 | 行范围 | 对白角色 | 开场摘录 | 收尾摘录 |",
            "|---|---|---|---|---|---|",
        ]
        for s in payload["scenes"]:
            loc = s["source_location"]
            span = f"{loc['line_start']}–{loc['line_end']}"
            chars = "、".join(s["dialogue_characters"]) or "—"

            def cell(t: str) -> str:
                return t.replace("\n", " ").replace("|", "｜")

            lines.append(
                f"| {s['scene_no']} | {cell(s['heading'])} | {span} | {cell(chars)} "
                f"| {cell(s['opening_excerpt'])} | {cell(s['closing_excerpt'])} |"
            )
    return "\n".join(lines) + "\n"


def scene_index_run(ws: Workspace, source_id: str) -> Path:
    """写 analysis/_scenes/<source_id>/<run_id>/scenes.json + scenes.md，返回 run 目录。"""
    ws.require()
    norm = ws.path("normalized", f"{source_id}.json")
    if not norm.exists():
        raise WritersRoomError(
            f"缺少 normalized/{source_id}.json",
            f"先运行 writersroom ingest <文件> 导入来源（--source-id {source_id}），再生成场次索引",
        )
    doc = read_json(norm)
    index = build_index(doc)
    base = ws.path("analysis", "_scenes", source_id)
    existing = {p.name for p in base.iterdir() if p.is_dir()} if base.exists() else set()
    run_id = new_run_id(existing)
    run_dir = base / run_id
    payload = {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "run_id": run_id,
        "source_id": source_id,
        "source_hash": sha256_file(norm),
        **index,
    }
    write_json(run_dir / "scenes.json", payload)
    (run_dir / "scenes.md").write_text(_render_scenes_md(payload), encoding="utf-8")
    return run_dir
