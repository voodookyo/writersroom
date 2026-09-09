"""项目档案 / 版本标签 / 写前锁定（docs/design/spec.md §4.4）。

- project.json：项目档案（schema 1），可更新字段见 PROFILE_FIELDS；locks 只经
  set_lock/unset_lock 管理，不接受 set_profile 直接改写。
- versions.json：版本标签表（schema 1），label 唯一，指向已有的 normalized/<source_id>.json。
- 写前锁定：八项（LOCK_KEYS），供 stage 等正文类阶段做前置校验。
"""
from __future__ import annotations

import re

from .core import WritersRoomError, Workspace, now_iso, read_json, write_json

PROFILE_FIELDS = (
    "title", "format", "genre", "audience", "platform",
    "logline", "goals", "constraints", "status",
)

LOCK_KEYS = (
    "premise", "structure", "character_choice", "causality",
    "relationship_stage", "event_coverage", "episode_hook", "production_boundary",
)

# 版本标签将作为目录名使用（analysis/<label>/），必须非空且路径安全
LABEL_RE = re.compile(r"^[A-Za-z0-9_\-一-鿿][A-Za-z0-9_\-.一-鿿]*$")

_FIELD_TYPES = {
    "title": str, "format": str, "audience": str, "platform": str,
    "logline": str, "status": str,
    "genre": list, "goals": list, "constraints": dict,
}


def validate_label(label: str) -> None:
    """版本标签校验：非空、路径安全（将作为 analysis/<label>/ 目录名）。"""
    if not label or not LABEL_RE.match(label):
        raise WritersRoomError(
            f"非法版本标签：{label!r}",
            "标签只支持中英文、数字、`_` `-` `.`，且不得以 `.` 开头（不能含路径分隔符）",
        )


# ---------- 项目档案 ----------

def set_profile(ws: Workspace, **fields) -> dict:
    """更新 project.json 的允许字段（PROFILE_FIELDS），返回更新后的 project dict。

    未知字段报 WritersRoomError；genre/goals 须为列表、constraints 须为字典、其余为字符串。
    """
    unknown = sorted(set(fields) - set(PROFILE_FIELDS))
    if unknown:
        raise WritersRoomError(
            f"未知档案字段：{', '.join(unknown)}",
            f"可更新字段：{', '.join(PROFILE_FIELDS)}；locks 请用 set_lock/unset_lock 管理",
        )
    for key, value in fields.items():
        expected = _FIELD_TYPES[key]
        if not isinstance(value, expected):
            raise WritersRoomError(
                f"档案字段 {key} 类型错误：应为 {expected.__name__}，收到 {type(value).__name__}",
                "genre/goals 传字符串列表，constraints 传字典，其余字段传字符串",
            )
    proj = ws.project()
    proj.update(fields)
    ws.save_project(proj)
    return proj


# ---------- 版本标签 ----------

def _load_versions(ws: Workspace) -> dict:
    p = ws.path("versions.json")
    if p.exists():
        return read_json(p)
    return {"schema": 1, "versions": []}


def version_add(ws: Workspace, source_id: str, label: str, note: str = "") -> dict:
    """登记版本标签：label 唯一，source_id 必须已有 normalized/<source_id>.json。"""
    ws.require()
    validate_label(label)
    if not ws.path("normalized", f"{source_id}.json").exists():
        raise WritersRoomError(
            f"source_id 无规范化文档：{source_id}",
            f"先运行 writersroom ingest 导入该材料（缺 normalized/{source_id}.json）",
        )
    data = _load_versions(ws)
    if any(v.get("label") == label for v in data["versions"]):
        raise WritersRoomError(
            f"版本标签已存在：{label}",
            "换一个标签；用 writersroom version list 查看现有标签",
        )
    entry = {"label": label, "source_id": source_id, "note": note, "created_at": now_iso()}
    data["versions"].append(entry)
    write_json(ws.path("versions.json"), data)
    return entry


def version_list(ws: Workspace) -> list[dict]:
    """按登记顺序返回版本标签列表。"""
    ws.require()
    return list(_load_versions(ws)["versions"])


# ---------- 写前锁定 ----------

def _check_lock_key(key: str) -> None:
    if key not in LOCK_KEYS:
        raise WritersRoomError(
            f"未知锁定项：{key}",
            f"锁定项限八项：{', '.join(LOCK_KEYS)}",
        )


def set_lock(ws: Workspace, key: str, value=True) -> dict:
    """锁定一项写前检查（value 默认 True，也可存说明字符串）；返回更新后的 project dict。"""
    _check_lock_key(key)
    proj = ws.project()
    proj.setdefault("locks", {})[key] = value
    ws.save_project(proj)
    return proj


def unset_lock(ws: Workspace, key: str) -> dict:
    """解除一项写前锁定（未锁定时为无操作）；返回更新后的 project dict。"""
    _check_lock_key(key)
    proj = ws.project()
    proj.get("locks", {}).pop(key, None)
    ws.save_project(proj)
    return proj
