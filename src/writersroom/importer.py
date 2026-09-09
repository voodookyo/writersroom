"""通用导入工具：混合命名目录 → 分类整理进工作区（docs/design/spec.md §4.8）。

规则要点（与 spec 一致）：
- 分类规则为数据化常量 CATEGORY_RULES（有序：文件名正则 → category）；
  md 文件 frontmatter 中的 ``category`` 字段优先于文件名规则；
- 集数前缀规范化：``E1`` / ``e01`` / ``第1集`` / ``第01集`` 等 → ``EP01``
  （保留原扩展名与前缀之后的其余名称）；
- 目标名 = ``<category>/<规范化名>``，相对于工作区根目录；
  目标已存在 → ``action=skip_exists``，绝不覆盖；
- 同一次扫描中多个源文件映射到同一目标名 → 先出现者 ``copy``，后者标记 ``conflict``；
- ``scan`` 只写 ``imports/<run_id>/plan.json``（即 CLI 的 dry-run，不产生其他副作用）；
  ``apply`` 内部复用 ``scan``（保证计划与执行一致），执行复制并写 ``report.md``。
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import frontmatter

from .core import (
    TOOL, WritersRoomError, Workspace, new_run_id, now_iso,
    sha256_file, sha256_text, write_json,
)

CATEGORIES = ("剧本", "大纲", "会议", "素材", "参考")

# 分类规则表（数据化常量）：(category, 文件名正则)，有序、先匹配先生效；
# 均不匹配 → DEFAULT_CATEGORY。md 的 frontmatter category 在本表之前判定（见 _classify）。
CATEGORY_RULES: list[tuple[str, re.Pattern]] = [
    ("剧本", re.compile(r"剧本|screenplay|\.fountain$", re.I)),
    ("大纲", re.compile(r"大纲|梗概|outline|treatment", re.I)),
    ("会议", re.compile(r"会议|纪要|围读|meeting", re.I)),
    ("参考", re.compile(r"参考|reference", re.I)),
]
DEFAULT_CATEGORY = "素材"

# 集数前缀：第1集 / 第01集 / E1 / e01（仅匹配行首，数字不限位数）
_RE_EPISODE_PREFIX = re.compile(r"^(?:第\s*(\d+)\s*集|E(\d+))", re.I)

_MD_SUFFIXES = (".md", ".markdown")


def normalize_episode_prefix(name: str) -> str:
    """文件名集数前缀规范化：E1|e01|第1集|第01集 → EP01；其余原样保留。"""
    m = _RE_EPISODE_PREFIX.match(name)
    if not m:
        return name
    n = int(next(g for g in m.groups() if g))
    return f"EP{n:02d}" + name[m.end():]


def _classify(path: Path) -> tuple[str, str]:
    """返回 (category, matched_by)；matched_by ∈ frontmatter / rule:<正则> / default。"""
    if path.suffix.lower() in _MD_SUFFIXES:
        try:
            meta = frontmatter.load(path).metadata
        except Exception:
            meta = {}
        cat = str(meta.get("category") or "").strip()
        if cat in CATEGORIES:
            return cat, "frontmatter"
    for category, pattern in CATEGORY_RULES:
        if pattern.search(path.name):
            return category, f"rule:{pattern.pattern}"
    return DEFAULT_CATEGORY, "default"


def _build_entries(ws: Workspace, src: Path) -> list[dict]:
    entries: list[dict] = []
    files = sorted((p for p in src.iterdir() if p.is_file()), key=lambda p: p.name)
    for path in files:
        category, matched_by = _classify(path)
        target_name = f"{category}/{normalize_episode_prefix(path.name)}"
        entries.append({
            "file": path.name,
            "sha256": sha256_file(path),
            "category": category,
            "matched_by": matched_by,
            "target_name": target_name,
            "action": "copy",
        })
    planned: set[str] = set()
    for e in entries:
        target = e["target_name"]
        if target in planned:
            e["action"] = "conflict"        # 同一目标名被多个源文件占用
        elif ws.path(target).exists():
            e["action"] = "skip_exists"     # 绝不覆盖既有文件
        planned.add(target)
    return entries


def _summarize(entries: list[dict]) -> dict:
    out = {"total": len(entries), "copy": 0, "skip_exists": 0, "conflict": 0}
    for e in entries:
        out[e["action"]] += 1
    return out


def scan(ws: Workspace, src_dir: str | Path) -> dict:
    """扫描 src_dir 生成导入计划；只写 imports/<run_id>/plan.json，不复制任何文件。"""
    ws.require()
    src = Path(src_dir)
    if not src.is_dir():
        raise WritersRoomError(
            f"导入来源不是目录：{src}",
            "import 只接受目录；把待整理文件放进一个目录后重试",
        )
    entries = _build_entries(ws, src)
    imports_dir = ws.path("imports")
    existing = {p.name for p in imports_dir.iterdir() if p.is_dir()} if imports_dir.exists() else set()
    run_id = new_run_id(existing)
    plan = {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "run_id": run_id,
        "source_dir": str(src),
        "source_hash": sha256_text("\n".join(f"{e['file']}:{e['sha256']}" for e in entries)),
        "files": entries,
        "summary": _summarize(entries),
    }
    write_json(imports_dir / run_id / "plan.json", plan)
    return plan


def _render_report_md(report: dict) -> str:
    lines = [
        "# 导入报告",
        "",
        f"- run_id：`{report['run_id']}`",
        f"- 来源目录：`{report['source_dir']}`",
        f"- 生成时间：{report['created_at']}（{report['produced_by']} 自动产物，绝不覆盖既有文件）",
        "",
        f"## 成功复制（{report['summary']['copied']}）",
    ]
    for r in report["copied"]:
        lines.append(f"- `{r['file']}` → `{r['target_name']}`")
    if not report["copied"]:
        lines.append("（无）")
    lines.append(f"\n## 跳过：目标已存在（{report['summary']['skip_exists']}）")
    for r in report["skipped"]:
        lines.append(f"- `{r['file']}` → `{r['target_name']}`")
    if not report["skipped"]:
        lines.append("（无）")
    lines.append(f"\n## 冲突：目标重名（{report['summary']['conflict']}）")
    for r in report["conflicts"]:
        lines.append(f"- `{r['file']}` → `{r['target_name']}`（与本计划内其他文件重名，未复制）")
    if not report["conflicts"]:
        lines.append("（无）")
    return "\n".join(lines) + "\n"


def apply(ws: Workspace, src_dir: str | Path) -> dict:
    """执行导入：内部先做 scan（写 plan.json），再复制文件并写 report.md。

    返回 report dict：copied / skipped / conflicts 三份清单与汇总计数。
    """
    plan = scan(ws, src_dir)
    src = Path(src_dir)
    copied: list[dict] = []
    skipped: list[dict] = []
    conflicts: list[dict] = []
    for e in plan["files"]:
        rec = {"file": e["file"], "target_name": e["target_name"]}
        if e["action"] == "conflict":
            conflicts.append(rec)
            continue
        target = ws.path(e["target_name"])
        if target.exists():
            skipped.append(rec)             # 扫描后目标又出现：仍绝不覆盖
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / e["file"], target)
        copied.append(rec)
    report = {
        "schema": 1,
        "produced_by": TOOL,
        "created_at": now_iso(),
        "run_id": plan["run_id"],
        "source_dir": plan["source_dir"],
        "source_hash": plan["source_hash"],
        "copied": copied,
        "skipped": skipped,
        "conflicts": conflicts,
        "summary": {
            "total": len(plan["files"]),
            "copied": len(copied),
            "skip_exists": len(skipped),
            "conflict": len(conflicts),
        },
    }
    run_dir = ws.path("imports", plan["run_id"])
    (run_dir / "report.md").write_text(_render_report_md(report), encoding="utf-8")
    return report
