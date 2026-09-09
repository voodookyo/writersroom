"""文档接收：txt/md/fountain/docx/pdf → 统一文档模型（docs/design/spec.md §3、§4.1）。

解析策略（与 spec §4.1 一致的最终取舍）：
- Fountain 使用自研解析器为主（规则见 parse_fountain docstring，覆盖中文场景头/角色名）；
  screenplain 仅作 `--parser screenplain` 显式指定的交叉验证后端（env 无关的确定性优先）。
- txt/docx/pdf 检测到中式剧本结构（场号「1—1」+景时+人物行 ≥3 处）时改用 parse_zh_screenplay
  做结构解析（页眉剔除、PDF 字间空格折叠），否则保持原段落路径，并附 FORMAT_NOTE 说明。
- docx 依赖 python-docx（可选 extras），缺失时报可操作错误。
- pdf 依赖 pypdf（钉 >=6.16.2），逐页异常隔离，无文本层且有嵌入图像 → SCANNED_PAGE 警告。
- 任何解析失败都转为 warnings 并保留已提取内容；失败不得伪装成空正文。
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

from .core import (
    TOOL, WritersRoomError, Workspace, now_iso, sha256_file, write_json,
)

BLOCK_KINDS = (
    "heading", "scene_heading", "action", "character", "dialogue",
    "parenthetical", "transition", "paragraph", "note",
)

SUPPORTED = ("txt", "md", "fountain", "docx", "pdf")

_EXT_MAP = {
    ".txt": "txt", ".md": "md", ".markdown": "md",
    ".fountain": "fountain", ".docx": "docx", ".pdf": "pdf",
}


def detect_format(path: Path) -> str:
    fmt = _EXT_MAP.get(path.suffix.lower())
    if not fmt:
        raise WritersRoomError(
            f"无法识别的文件类型：{path.name}",
            f"支持的扩展名：{', '.join(sorted(_EXT_MAP))}；或用 --format 显式指定",
        )
    return fmt


# ---------- 文本读取（编码回退） ----------

def read_text_file(path: Path) -> tuple[str, list[dict]]:
    warnings: list[dict] = []
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8"), warnings
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "big5"):
        try:
            text = raw.decode(enc)
            warnings.append({
                "code": "ENCODING_FALLBACK",
                "detail": f"UTF-8 解码失败，已回退 {enc} 解码；建议转换为 UTF-8 后重新导入",
                "location": None,
            })
            return text, warnings
        except UnicodeDecodeError:
            continue
    raise WritersRoomError(
        f"无法解码文本文件：{path.name}",
        "请用 UTF-8 或 GB18030 编码保存后重试",
    )


# ---------- 块构造 ----------

class _BlockBuilder:
    def __init__(self) -> None:
        self.blocks: list[dict] = []

    def add(self, kind: str, text: str, line_start: int, line_end: int | None = None,
            scene_no: str | None = None, episode: str | None = None, page: int | None = None) -> None:
        assert kind in BLOCK_KINDS, f"未知块类型 {kind}"
        self.blocks.append({
            "index": len(self.blocks),
            "kind": kind,
            "text": text,
            "line_start": line_start,
            "line_end": line_end if line_end is not None else line_start,
            "scene_no": scene_no,
            "episode": episode,
            "page": page,
        })


# ---------- Fountain 解析（自研；规则文档化） ----------

# 规则（按优先级逐行判定）：
#  1. 空行：结束当前角色/对白上下文
#  2. 集/章标题：# 开头、第N集/章/回/部、EPN/EpN
#  3. 强制场景头：`.` 开头（Fountain 规范）
#  4. 中文场景头：内景/外景/内外景/内/外/内外 + [.．、\s／/]
#  5. 英文场景头：INT/EXT/EST/INT./EXT/INT/EXT/I/E + [. ]
#  6. 场号：场景头尾部 #…#；或行首「数字+./、」；或「场N」
#  7. 转场：拉丁全大写且 TO: 结尾；`>` 强制；中文转场词独占一行
#  8. 角色：@ 强制；或短行（≤20）全大写拉丁/纯中文名（可带括注），前为空行/场景/动作，后为非空行
#  9. 括注：角色/对白后紧跟的 （…）/（…）行
# 10. 注释：[[…]]
# 11. 角色后的连续非空行为对白
# 12. 其余非空行：fountain→action；txt/md→paragraph

_RE_MD_HEADING = re.compile(r"^#{1,6}\s+")
_RE_EPISODE = re.compile(r"^(?:第\s*([0-9零一二三四五六七八九十百]+)\s*[集章回部]|EP?\s*(\d+))", re.I)
_RE_FORCE_SCENE = re.compile(r"^\.(?!\.)")
_RE_SCENE_ZH = re.compile(r"^(内景|外景|内外景|内外|内|外)\s*[.．、／/]")
_RE_SCENE_EN = re.compile(r"^(INT\.?/EXT\.?|INT|EXT|EST|I/E)[. ]", re.I)
_RE_SCENE_NO_TAIL = re.compile(r"#([^#]+)#\s*$")
_RE_SCENE_NO_HEAD = re.compile(r"^(\d+)\s*[.、]")
_RE_SCENE_NO_MARK = re.compile(r"场\s*(\d+)")
_RE_TRANSITION_EN = re.compile(r"^[A-Z][A-Z0-9 '\-]*TO:$")
_RE_FORCE_TRANSITION = re.compile(r"^>\s*")
_TRANSITION_ZH = ("切至", "淡入", "淡出", "黑场", "闪回", "闪回结束", "跳切", "叠化", "转场")
_RE_CHARACTER_FORCED = re.compile(r"^@(.+)$")
_RE_CHARACTER_LATIN = re.compile(r"^[A-Z][A-Z0-9 .·'\-]*(\(.*\))?\s*$")
_RE_CHARACTER_ZH = re.compile(r"^[一-鿿][一-鿿A-Za-z0-9·]*\s*([（(][^）)]*[）)])?$")
_RE_PAREN = re.compile(r"^[（(].*[）)]$")
_RE_NOTE = re.compile(r"^\[\[.*\]\]$")


def _extract_scene_no(text: str) -> str | None:
    m = _RE_SCENE_NO_TAIL.search(text)
    if m:
        return m.group(1).strip()
    m = _RE_SCENE_NO_HEAD.match(text)
    if m:
        return m.group(1)
    m = _RE_SCENE_NO_MARK.search(text)
    if m:
        return m.group(1)
    return None


def parse_fountain(text: str) -> tuple[list[dict], list[dict]]:
    """自研 Fountain+中文扩展解析器。返回 (blocks, warnings)。"""
    bb = _BlockBuilder()
    warnings: list[dict] = []
    lines = text.splitlines()
    state = "free"  # free | character | dialogue
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        lineno = i + 1
        i += 1
        if not line:
            state = "free"
            continue

        m = _RE_MD_HEADING.match(line) or _RE_EPISODE.match(line)
        if m:
            episode = None
            em = _RE_EPISODE.match(line)
            if em:
                episode = next(g for g in em.groups() if g)
            bb.add("heading", line, lineno, episode=episode)
            state = "free"
            continue
        if _RE_FORCE_SCENE.match(line):
            content = line.lstrip(".").strip()
            bb.add("scene_heading", content, lineno, scene_no=_extract_scene_no(content))
            state = "free"
            continue
        if _RE_SCENE_ZH.match(line) or _RE_SCENE_EN.match(line):
            bb.add("scene_heading", line, lineno, scene_no=_extract_scene_no(line))
            state = "free"
            continue
        if _RE_TRANSITION_EN.match(line) or (_RE_FORCE_TRANSITION.match(line) and len(line) < 40) \
                or line in _TRANSITION_ZH:
            bb.add("transition", line.lstrip(">"), lineno)
            state = "free"
            continue
        if _RE_NOTE.match(line):
            bb.add("note", line.strip("[]"), lineno)
            continue
        if _RE_PAREN.match(line) and state in ("character", "dialogue"):
            bb.add("parenthetical", line, lineno)
            continue

        is_forced_char = _RE_CHARACTER_FORCED.match(line)
        is_char = False
        name = line
        if is_forced_char:
            is_char, name = True, is_forced_char.group(1).strip()
        elif state == "free" and len(line) <= 20 and i < len(lines) and lines[i].strip():
            if _RE_CHARACTER_LATIN.match(line) or _RE_CHARACTER_ZH.match(line):
                # 排除误判：整行以句读结尾视为动作
                if not re.search(r"[。！？!?…]$", line):
                    is_char = True
        if is_char:
            bb.add("character", name, lineno)
            state = "character"
            continue
        if state in ("character", "dialogue"):
            bb.add("dialogue", line, lineno)
            state = "dialogue"
            continue
        bb.add("action", line, lineno)
        state = "free"
    return bb.blocks, warnings


# ---------- txt / md ----------

def parse_txt(text: str) -> tuple[list[dict], list[dict]]:
    raw_lines = text.splitlines()
    if looks_like_zh_screenplay([ln.strip() for ln in raw_lines]):
        return parse_zh_screenplay([(ln, i + 1, None) for i, ln in enumerate(raw_lines)])
    bb = _BlockBuilder()
    for m in re.finditer(r"[^\n]+(?:\n(?!\n)[^\n]+)*", text):
        para = m.group(0).strip()
        if not para:
            continue
        line_start = text[:m.start()].count("\n") + 1
        line_end = text[:m.end()].count("\n") + 1
        first = para.splitlines()[0].strip()
        em = _RE_EPISODE.match(first)
        if em:
            episode = next((g for g in em.groups() if g), None)
            bb.add("heading", first, line_start, line_start, episode=episode)
            rest = "\n".join(para.splitlines()[1:]).strip()
            if rest:
                bb.add("paragraph", rest, line_start + 1, line_end)
        else:
            bb.add("paragraph", para, line_start, line_end)
    return bb.blocks, []


def parse_md(text: str) -> list[dict]:
    bb = _BlockBuilder()
    blocks_raw = re.split(r"\n\s*\n", text)
    pos = 0
    for para in blocks_raw:
        start = text.find(para, pos)
        if start < 0:
            continue
        pos = start + len(para)
        stripped = para.strip()
        if not stripped:
            continue
        line_start = text[:start].count("\n") + 1
        line_end = line_start + stripped.count("\n")
        if _RE_MD_HEADING.match(stripped):
            title = stripped.lstrip("#").strip()
            em = _RE_EPISODE.match(title)
            episode = next((g for g in em.groups() if g), None) if em else None
            bb.add("heading", title, line_start, line_end, episode=episode)
        else:
            bb.add("paragraph", stripped, line_start, line_end)
    return bb.blocks


# ---------- 中式剧本（场号 + 景时 + 人物 格式） ----------
#
# 与 Fountain 并列的第二种剧本结构解析器，面向国内常见排版：
#   第01集                  → 集标题（沿用 _RE_EPISODE）
#   1—1                     → 场号（数字-数字，兼容 - – — ―），与随后的景时行合成场景头
#   景时：地点、日/夜、内/外  → 场景头
#   人物：甲、乙             → 本场出场（并入场景头元数据，并建立全局角色名册）
#   甲（OS）：台词           → 角色 + 对白（仅当名字在名册内才判定，防「她拿起电话：……」类动作行误判）
#   【字幕：…】              → 注释
# 判定入口：≥3 个结构标记才按中式剧本解析，否则保持原段落路径（不猜格式）。
# PDF 抽取副作用在此一并清理：页眉「第 N 页 共 M 页」、重复书名行、CJK 字间空格。
# 对白折行（PDF 换行无空行）并入上一对白块；空行永远结束对白上下文。
# 折行判定以句末标点为闸：上一对白块以 。！？… 等收束即不再并入
# （本格式抽取结果中段间无空行，仅靠换行无法区分折行与新动作行）。

_RE_ZH_SCENE_NO = re.compile(r"^(\d{1,3})\s*[—–\-―]\s*(\d{1,3})$")
_RE_ZH_JINGSHI = re.compile(r"^景\s*时\s*[:：]\s*(.+)$")
_RE_ZH_RENWU = re.compile(r"^人\s*物\s*[:：]\s*(.+)$")
_RE_ZH_CAPTION = re.compile(r"^【[^】]*】$")
_RE_ZH_PAGE_HEADER = re.compile(r"^第\s*\d+\s*页(?:\s*共\s*\d+\s*页?)?\s*$")
_RE_ZH_TITLE = re.compile(r"^《[^》]+》\s*剧本?\s*$")
_RE_ZH_SPEECH = re.compile(r"^([一-鿿A-Za-z0-9·]{1,12}?)\s*(?:（[^）]*）|\([^)]*\))?\s*[:：]\s*(.*)$")
_RE_CJK_SPACE = re.compile(
    r"(?<=[一-鿿，。？！：；、（）「」…—])\s+(?=[一-鿿，。？！：；、（）「」…—])")
_ZH_TERMINAL = "。！？…”’」』~"


def _norm_compat_ideographs(text: str) -> tuple[str, set[str]]:
    """康熙部首/CJK 部首补充（U+2E80–2FDF）与 CJK 兼容汉字（U+F900–FAFF）映射回标准汉字。

    某些 PDF 字体把「高/心/文」嵌成兼容字形（⾼/⼼/⽂），不做映射会让
    角色名册与对白正则整体失灵（实测：整集对白归零）。只动这三段码位，
    标点全角半角保持原样。无标准映射的字形保持原样并如实上报（不静默）。
    """
    unmappable: set[str] = set()
    out: list[str] = []
    for ch in text:
        o = ord(ch)
        if 0x2E80 <= o <= 0x2FDF or 0xF900 <= o <= 0xFAFF:
            mapped = unicodedata.normalize("NFKC", ch)
            if mapped == ch:
                unmappable.add(ch)
            out.append(mapped)
        else:
            out.append(ch)
    return "".join(out), unmappable


def _zh_roster(lines: list[str]) -> set[str]:
    """从全部「人物：」行建立全局角色名册（顺序无关，先声明后发言不成立也能识别）。"""
    roster: set[str] = set()
    for ln in lines:
        m = _RE_ZH_RENWU.match(ln)
        if m:
            for name in re.split(r"[、,，/／\s]+", m.group(1)):
                # 括注内容常是真名/身份，如「管家男（老赵）」「两个青年（便衣保安）」
                for inner in re.findall(r"[（(]([^）)]*)[）)]", name):
                    if inner.strip():
                        roster.add(inner.strip())
                name = re.sub(r"[（(][^）)]*[）)]", "", name).strip()
                # 「三个男高管和阿珍」类连写再按连词拆开，提升名册召回
                for part in re.split(r"[和与]", name):
                    if part:
                        roster.add(part)
    return roster


def looks_like_zh_screenplay(lines: list[str]) -> bool:
    """≥3 个结构标记（场号/景时/人物）才按中式剧本解析。"""
    n = sum(1 for ln in lines
            if _RE_ZH_SCENE_NO.match(ln) or _RE_ZH_JINGSHI.match(ln) or _RE_ZH_RENWU.match(ln))
    return n >= 3


_ZH_FORMAT_NOTE = {
    "code": "FORMAT_NOTE",
    "detail": "检测到中式剧本结构（场号/景时/人物），已按剧本块解析；页眉与 PDF 字间空格已清理",
    "location": None,
}


def parse_zh_screenplay(lines: list[tuple[str, int, int | None]]) -> tuple[list[dict], list[dict]]:
    """lines: [(原始行文本, 行号, 页码或 None)]，空行需保留（它是对白上下文的边界）。

    返回 (blocks, warnings)；warnings 固定含一条 FORMAT_NOTE，说明发生了格式判定。
    """
    bb = _BlockBuilder()
    # 名册必须在折叠前建立：「人物：沈舟 林晚」的空格是分隔符，折叠后会粘成一个名字
    raw_texts: list[str] = []
    unmappable: set[str] = set()
    for t, _, _ in lines:
        nt, un = _norm_compat_ideographs(t)
        raw_texts.append(nt.strip())
        unmappable |= un
    roster = _zh_roster(raw_texts)
    cleaned = [(_RE_CJK_SPACE.sub("", t).strip(), ln, pg) for t, ln, pg in
               zip(raw_texts, (ln for _, ln, _ in lines), (pg for _, _, pg in lines))]
    pending_scene_no: str | None = None   # 场号行已读、场景头未落地
    current_char: str | None = None       # 对白续行归属
    after_page_header = False             # 页眉「第 N 页 共 M」折行残留「页」待剥离
    title_seen = False
    for text, lineno, page in cleaned:
        if not text:
            current_char = None
            continue
        if _RE_ZH_PAGE_HEADER.match(text):
            after_page_header = True
            continue
        if after_page_header:
            # 「第 N 页 共 M\n页<正文>」：剥离折行残留的一个「页」字
            after_page_header = False
            if text == "页":
                continue
            if text.startswith("页"):
                text = text[1:]
        if _RE_ZH_TITLE.match(text):
            if not title_seen:
                bb.add("note", text, lineno, page=page)
                title_seen = True
            continue
        em = _RE_EPISODE.match(text)
        if em:
            episode = next(g for g in em.groups() if g)
            bb.add("heading", text, lineno, page=page, episode=episode)
            current_char = None
            continue
        if _RE_ZH_CAPTION.match(text):
            bb.add("note", text, lineno, page=page)
            current_char = None
            continue
        m = _RE_ZH_SCENE_NO.match(text)
        if m:
            pending_scene_no = f"{int(m.group(1))}-{int(m.group(2))}"
            current_char = None
            continue
        m = _RE_ZH_JINGSHI.match(text)
        if m:
            head = f"{pending_scene_no} 景时：{m.group(1)}" if pending_scene_no \
                else f"景时：{m.group(1)}"
            bb.add("scene_heading", head, lineno, page=page, scene_no=pending_scene_no)
            pending_scene_no = None
            current_char = None
            continue
        m = _RE_ZH_RENWU.match(text)
        if m:
            content = m.group(1)
            # 该行已经过了 CJK 空格折叠（「沈舟 林晚」→「沈舟林晚」），按名册成员回拆
            names = [r for r in sorted(roster, key=len, reverse=True) if r and r in content]
            names.sort(key=content.find)  # 保持原文出现顺序
            if not names:
                names = [n for n in re.split(r"[、,，/／\s]+", content) if n]
            if pending_scene_no is not None:
                bb.add("scene_heading", pending_scene_no, lineno, page=page,
                       scene_no=pending_scene_no)
                pending_scene_no = None
            if bb.blocks and bb.blocks[-1]["kind"] == "scene_heading":
                bb.blocks[-1]["declared_chars"] = names
            else:
                bb.add("note", text, lineno, page=page)
            current_char = None
            continue
        if pending_scene_no is not None:
            bb.add("scene_heading", pending_scene_no, lineno, page=page,
                   scene_no=pending_scene_no)
            pending_scene_no = None
        if text in _TRANSITION_ZH:
            bb.add("transition", text, lineno, page=page)
            current_char = None
            continue
        sm = _RE_ZH_SPEECH.match(text)
        if sm:
            name = sm.group(1)
            if name not in roster:
                # 名册前缀 + ≤3 字语气修饰（「拍卖员兴奋地：」「林晚笑：」）归并到名册角色；
                # 修饰过长（「女管家靠门口打电话：」）保持动作判定
                name = next((r for r in roster
                             if len(r) >= 2 and name.startswith(r) and len(name) - len(r) <= 3),
                            None)
            if name:
                bb.add("character", name, lineno, page=page)
                if sm.group(2):
                    bb.add("dialogue", sm.group(2), lineno, page=page)
                current_char = name
                continue
        if current_char is not None and bb.blocks:
            prev = bb.blocks[-1]
            if prev["kind"] == "dialogue" and not prev["text"].endswith(tuple(_ZH_TERMINAL)):
                prev["text"] += text
                prev["line_end"] = lineno
                continue
            if prev["kind"] == "character":
                bb.add("dialogue", text, lineno, page=page)
                continue
        bb.add("action", text, lineno, page=page)
        current_char = None
    warnings: list[dict] = [dict(_ZH_FORMAT_NOTE)]
    if unmappable:
        warnings.append({
            "code": "COMPAT_IDEOGRAPH_UNMAPPED",
            "detail": "以下兼容字形无标准映射，已保持原样："
                      + "、".join(f"U+{ord(c):04X}" for c in sorted(unmappable)),
            "location": None,
        })
    return bb.blocks, warnings


# ---------- docx / pdf ----------

def parse_docx(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        import docx  # python-docx，可选 extras
    except ImportError as e:
        raise WritersRoomError(
            "缺少 python-docx，无法读取 DOCX",
            "pip install 'writersroom[docx]' 或 pip install python-docx",
        ) from e
    warnings: list[dict] = []
    try:
        document = docx.Document(str(path))
    except Exception as e:  # 损坏/加密等
        raise WritersRoomError(f"DOCX 解析失败：{path.name}（{e}）",
                               "确认文件未损坏、未加密；必要时另存为新 DOCX") from e
    bb = _BlockBuilder()
    paras: list[tuple[str, int, None]] = []
    for idx, para in enumerate(document.paragraphs):
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "") if para.style is not None else ""
        if style.startswith(("Heading", "标题")):
            em = _RE_EPISODE.match(text)
            episode = next((g for g in em.groups() if g), None) if em else None
            bb.add("heading", text, idx + 1, episode=episode)
        else:
            paras.append((text, idx + 1, None))
    if looks_like_zh_screenplay([t for t, _, _ in paras]):
        # Heading 块已先入列；中式剧本文体段落改走结构解析，拼回并按行号排序
        zh_blocks, w = parse_zh_screenplay(paras)
        blocks = sorted(bb.blocks + zh_blocks, key=lambda b: b["line_start"])
        for i, b in enumerate(blocks):
            b["index"] = i
        return blocks, warnings + w
    for text, lineno, _ in paras:
        bb.add("paragraph", text, lineno)
    if not bb.blocks:
        warnings.append({"code": "EMPTY_TEXT", "detail": "DOCX 正文段落为空", "location": None})
    return bb.blocks, warnings


_PDF_PAGE_GUARD_BYTES = 64 * 1024 * 1024  # 单页内容流上限，防 DoS 类畸形 PDF（见威胁模型）


def parse_pdf(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # 不应发生（pypdf 为硬依赖），防御性处理
        raise WritersRoomError("缺少 pypdf", "pip install 'pypdf>=6.16.2'") from e
    warnings: list[dict] = []
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise WritersRoomError(f"PDF 已加密：{path.name}",
                                       "请先解密后再导入") from None
    except WritersRoomError:
        raise
    except Exception as e:
        raise WritersRoomError(f"PDF 打开失败：{path.name}（{e}）",
                               "确认文件未损坏；必要时用阅读器另存") from e
    bb = _BlockBuilder()
    line_cursor = 1
    raw_lines: list[tuple[str, int, int]] = []  # (行文本, 行号, 页码)，含空行（中式剧本对白边界）
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            contents = page.get_contents()
            if contents is not None:
                data = contents.get_data()
                if data is not None and len(data) > _PDF_PAGE_GUARD_BYTES:
                    warnings.append({"code": "PARSE_ERROR",
                                     "detail": f"第 {page_no} 页内容流超过 64MB，已跳过（安全护栏）",
                                     "location": page_no})
                    continue
            text = (page.extract_text() or "").strip()
        except Exception as e:
            warnings.append({"code": "PARSE_ERROR",
                             "detail": f"第 {page_no} 页提取失败：{e}", "location": page_no})
            continue
        if not text:
            try:
                has_images = len(page.images) > 0
            except Exception:
                has_images = False
            code = "SCANNED_PAGE" if has_images else "EMPTY_TEXT"
            detail = (f"第 {page_no} 页无文本层且含嵌入图像，疑似扫描件；"
                      "本工具不做 OCR，请转文字后重新导入") if has_images else \
                     f"第 {page_no} 页无文本层（可能为纯矢量图或空白页）"
            warnings.append({"code": code, "detail": detail, "location": page_no})
            continue
        for ln in text.split("\n"):
            raw_lines.append((ln.strip(), line_cursor, page_no))
            line_cursor += 1
    if looks_like_zh_screenplay([t for t, _, _ in raw_lines]):
        zh_blocks, w = parse_zh_screenplay(raw_lines)
        warnings.extend(w)
        return zh_blocks, warnings
    for ln, cursor, page_no in raw_lines:
        if not ln:
            continue
        bb.add("paragraph", ln, cursor, page=page_no)
    if not bb.blocks and not warnings:
        warnings.append({"code": "EMPTY_TEXT", "detail": "PDF 未提取到任何文本", "location": None})
    return bb.blocks, warnings


# ---------- 统一入口 ----------

def parse_document(path: Path, fmt: str = "auto") -> dict:
    """解析文件为文档模型（不含 source 段，由 ingest 补齐）。"""
    if fmt == "auto":
        fmt = detect_format(path)
    if fmt not in SUPPORTED:
        raise WritersRoomError(f"不支持的格式 {fmt}", f"支持：{', '.join(SUPPORTED)}")

    warnings: list[dict] = []
    blocks: list[dict]
    if fmt in ("txt", "md", "fountain"):
        text, w = read_text_file(path)
        warnings.extend(w)
        if fmt == "fountain":
            blocks, w = parse_fountain(text)
        elif fmt == "md":
            blocks, w = parse_md(text), []
        else:
            blocks, w = parse_txt(text)
        warnings.extend(w)
    elif fmt == "docx":
        blocks, w = parse_docx(path)
        warnings.extend(w)
        text = "\n\n".join(b["text"] for b in blocks)
    else:  # pdf
        blocks, w = parse_pdf(path)
        warnings.extend(w)
        text = "\n\n".join(b["text"] for b in blocks)

    if not blocks and not any(x["code"] == "EMPTY_TEXT" for x in warnings):
        warnings.append({"code": "EMPTY_TEXT",
                         "detail": "未提取到任何内容块；若文件不为空请检查格式", "location": None})
    return {"format": fmt, "text": text, "blocks": blocks, "warnings": warnings}


def ingest(ws: Workspace, file_path: str | Path, source_id: str | None = None,
           fmt: str = "auto") -> tuple[str, dict]:
    """导入文件到工作区：sources/<id>/original + provenance.json + normalized/<id>.json。

    不覆盖既有 source_id（证据不可变）。
    """
    ws.require()
    src = Path(file_path)
    if not src.exists():
        raise WritersRoomError(f"文件不存在：{src}", "检查路径；相对路径以当前目录为基准")
    source_id = source_id or re.sub(r"[^\w\-一-鿿]+", "_", src.stem)
    dest_dir = ws.path("sources", source_id)
    if dest_dir.exists():
        raise WritersRoomError(
            f"source_id 已存在：{source_id}",
            "换一个 --source-id；原始材料不可变，如需重导入请先手动移除 sources/ 下对应目录",
        )
    parsed = parse_document(src, fmt)

    dest_dir.mkdir(parents=True)
    original = dest_dir / f"original{src.suffix.lower()}"
    shutil.copyfile(src, original)
    write_json(dest_dir / "provenance.json", {
        "schema": 1,
        "id": source_id,
        "original_name": src.name,
        "original_path": ws.rel(original),
        "format": parsed["format"],
        "sha256": sha256_file(original),
        "imported_at": now_iso(),
        "tool": TOOL,
    })
    doc = {
        "schema": 1,
        "source": {
            "id": source_id,
            "original_path": ws.rel(original),
            "format": parsed["format"],
            "sha256": sha256_file(original),
            "imported_at": now_iso(),
            "tool": TOOL,
        },
        "warnings": parsed["warnings"],
        "blocks": parsed["blocks"],
        "text": parsed["text"],
    }
    write_json(ws.path("normalized", f"{source_id}.json"), doc)
    return source_id, doc
