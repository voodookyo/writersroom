"""核心公共层：错误、时间、哈希、JSON/JSONL、工作区、分词、词表加载。

本模块只依赖标准库，是所有其他模块的共享地基。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path

from . import __version__

TOOL = f"writersroom {__version__}"


class WritersRoomError(Exception):
    """用户/环境错误。message 描述问题，hint 给出可操作的解决步骤。"""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint

    def render(self) -> str:
        out = f"错误：{self.message}"
        if self.hint:
            out += f"\n解决：{self.hint}"
        return out


# ---------- 时间与哈希 ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_run_id(existing: set[str] | None = None) -> str:
    """UTC 时间戳 run id；同秒冲突时追加 -2、-3…"""
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    existing = existing or set()
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------- JSON / JSONL（确定性输出：排序键、UTF-8、结尾换行） ----------

def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------- 工作区 ----------

WORKSPACE_DIRS = [
    "sources", "normalized", "analysis", "memory", "tasks",
    "meetings", "knowledge", "stages", "imports", "exports",
]

PROJECT_TEMPLATE = {
    "schema": 1,
    "title": "",
    "format": "",        # 形态：电影/剧集/短片…
    "genre": [],         # 类型
    "audience": "",      # 受众
    "platform": "",      # 平台
    "logline": "",
    "goals": [],         # 创作目标
    "constraints": {},   # 限制，如 {"episodes": 12, "page_chars": 500}
    "locks": {},         # 写前锁定项：premise/structure/character_choice/causality/
                         # relationship_stage/event_coverage/episode_hook/production_boundary
    "status": "开发中",
    "created_at": "",
    "updated_at": "",
}


class Workspace:
    """一个 writersroom 工作区 = 一个项目目录。布局见 docs/design/spec.md 第 1 节。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @classmethod
    def init(cls, root: str | Path, title: str = "") -> "Workspace":
        ws = cls(root)
        ws.root.mkdir(parents=True, exist_ok=True)
        for d in WORKSPACE_DIRS:
            ws.path(d).mkdir(exist_ok=True)
        pj = ws.path("project.json")
        if not pj.exists():
            proj = dict(PROJECT_TEMPLATE)
            proj["title"] = title
            proj["created_at"] = proj["updated_at"] = now_iso()
            write_json(pj, proj)
        vj = ws.path("versions.json")
        if not vj.exists():
            write_json(vj, {"schema": 1, "versions": []})
        return ws

    def require(self) -> None:
        if not self.path("project.json").exists():
            raise WritersRoomError(
                f"{self.root} 不是 writersroom 工作区（缺 project.json）",
                f"先运行 writersroom init {self.root} 创建工作区",
            )

    def rel(self, p: Path) -> str:
        try:
            return str(Path(p).resolve().relative_to(self.root.resolve()))
        except ValueError:
            return str(p)

    def project(self) -> dict:
        self.require()
        return read_json(self.path("project.json"))

    def save_project(self, proj: dict) -> None:
        proj["updated_at"] = now_iso()
        write_json(self.path("project.json"), proj)


# ---------- 文本工具：分句、分词 ----------

_SENT_SPLIT = re.compile(r"[^。！？!?…\n]+[。！？!?…]+|[^。！？!?…\n]+")


def split_sentences(text: str) -> list[str]:
    """按中英文句读切句；返回非空句列表。"""
    return [m.group(0).strip() for m in _SENT_SPLIT.finditer(text) if m.group(0).strip()]


_ASCII_WORD = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def tokenize(text: str, method: str = "auto") -> tuple[list[str], str]:
    """返回 (tokens, 实际使用的方法)。

    - char_ngram（默认）：ASCII 词小写化 + CJK 字 unigram 与相邻 bigram，零依赖、完全确定。
    - jieba：调用可选依赖 jieba 精确模式；未安装时回退 char_ngram 并在方法名中标注。
    """
    if method == "jieba" or (method == "auto" and _jieba_available()):
        try:
            import jieba  # 可选依赖，见 docs/research/candidate-matrix.md 域 E

            toks = [t.strip().lower() for t in jieba.cut(text) if t.strip()]
            toks = [t for t in toks if _ASCII_WORD.fullmatch(t) or any(_is_cjk(c) for c in t)]
            return toks, "jieba"
        except ImportError:
            if method == "jieba":
                # 显式指定但缺失：回退并标注，不伪造
                pass
    chars = [c for c in text if _is_cjk(c)]
    uni = chars
    bi = ["".join(chars[i:i + 2]) for i in range(len(chars) - 1)]
    words = [w.lower() for w in _ASCII_WORD.findall(text)]
    used = "char_ngram" if method in ("auto", "char_ngram") else f"char_ngram(fallback_from_{method})"
    return uni + bi + words, used


_JIEBA_CHECKED: bool | None = None


def _jieba_available() -> bool:
    global _JIEBA_CHECKED
    if _JIEBA_CHECKED is None:
        try:
            import jieba  # noqa: F401

            _JIEBA_CHECKED = True
        except ImportError:
            _JIEBA_CHECKED = False
    return _JIEBA_CHECKED


# ---------- 词表 ----------

def load_lexicon(name: str) -> list[str]:
    """加载 src/writersroom/data/lexicons/<name>；# 开头为注释。词表为自研启发式，见文件头声明。"""
    ref = resources.files("writersroom.data.lexicons").joinpath(name)
    text = ref.read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]


def load_project_lexicon(ws: Workspace, name: str) -> list[str]:
    """项目可用 knowledge/lexicons/<name> 覆盖内置词表。"""
    override = ws.path("knowledge", "lexicons", name)
    if override.exists():
        text = override.read_text(encoding="utf-8")
        return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    return load_lexicon(name)
