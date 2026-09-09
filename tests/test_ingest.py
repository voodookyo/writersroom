"""ingest.py 测试（spec §3/§4.1）：五格式 + 中式剧本格式 + 警告路径。"""
import re
from collections import Counter
from pathlib import Path

import pytest

from writersroom.core import Workspace, WritersRoomError, read_json
from writersroom.ingest import ingest, parse_document

MINI = "samples/mini.fountain"


@pytest.fixture
def ws(tmp_path):
    return Workspace.init(tmp_path / "ws", "测试")


def test_fountain_blocks(ws):
    sid, doc = ingest(ws, MINI, "ep1")
    kinds = {b["kind"] for b in doc["blocks"]}
    assert {"heading", "scene_heading", "character", "dialogue", "action"} <= kinds
    assert sid == "ep1" and doc["source"]["sha256"]
    assert read_json(ws.path("normalized", "ep1.json"))["schema"] == 1
    assert (ws.path("sources", "ep1", "original.fountain")).exists()


def test_txt_and_md(tmp_path):
    txt = tmp_path / "a.txt"
    txt.write_text("第1集\n\n第一段。\n\n第二段。", encoding="utf-8")
    doc = parse_document(txt)
    assert doc["format"] == "txt"
    assert any(b["kind"] == "heading" and b["episode"] == "1" for b in doc["blocks"])

    md = tmp_path / "b.md"
    md.write_text("# 大纲\n\n正文段落。\n\n## 第二节\n\n更多。", encoding="utf-8")
    doc = parse_document(md)
    heads = [b for b in doc["blocks"] if b["kind"] == "heading"]
    assert len(heads) == 2 and heads[0]["text"] == "大纲"


def test_encoding_fallback(tmp_path):
    f = tmp_path / "gbk.txt"
    f.write_bytes("中文内容，非 UTF-8 编码。".encode("gb18030"))
    doc = parse_document(f)
    assert any(w["code"] == "ENCODING_FALLBACK" for w in doc["warnings"])
    assert "中文内容" in doc["text"]


def test_docx(tmp_path):
    docx_mod = pytest.importorskip("docx")
    d = docx_mod.Document()
    d.add_heading("第1集", level=1)
    d.add_paragraph("内景. 房间 日")
    d.add_paragraph("一段动作。")
    f = tmp_path / "a.docx"
    d.save(str(f))
    doc = parse_document(f)
    assert doc["format"] == "docx"
    assert any(b["kind"] == "heading" for b in doc["blocks"])
    assert len([b for b in doc["blocks"] if b["kind"] == "paragraph"]) == 2


def _make_pdf(path: Path, with_text: bool):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    if with_text:
        c.drawString(72, 800, "Episode One. INT. ROOM - DAY")
    else:
        c.drawImage(_tiny_png(path.parent), 72, 700, width=50, height=50)
    c.showPage()
    c.save()


def _tiny_png(tmp) -> str:
    from PIL import Image

    p = tmp / "_px.png"
    Image.new("RGB", (4, 4), (0, 0, 0)).save(str(p))
    return str(p)


def test_pdf_text_and_scanned(tmp_path):
    pytest.importorskip("pypdf")
    pytest.importorskip("reportlab")
    pytest.importorskip("PIL")
    text_pdf = tmp_path / "text.pdf"
    _make_pdf(text_pdf, with_text=True)
    doc = parse_document(text_pdf)
    assert doc["blocks"], "文本型 PDF 应提取出内容块"
    assert not [w for w in doc["warnings"] if w["code"] == "SCANNED_PAGE"]

    scan_pdf = tmp_path / "scan.pdf"
    _make_pdf(scan_pdf, with_text=False)
    doc = parse_document(scan_pdf)
    assert any(w["code"] == "SCANNED_PAGE" and w["location"] == 1 for w in doc["warnings"])


def test_duplicate_source_id_rejected(ws):
    ingest(ws, MINI, "dup")
    with pytest.raises(WritersRoomError) as ei:
        ingest(ws, MINI, "dup")
    assert "已存在" in ei.value.message and ei.value.hint


def test_unknown_extension(tmp_path):
    f = tmp_path / "x.xyz"
    f.write_text("hi", encoding="utf-8")
    with pytest.raises(WritersRoomError) as ei:
        parse_document(f)
    assert "无法识别" in ei.value.message


def test_empty_file_warns_not_silent(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("", encoding="utf-8")
    doc = parse_document(f)
    assert doc["blocks"] == []
    assert any(w["code"] == "EMPTY_TEXT" for w in doc["warnings"])


def test_block_invariants(ws):
    _, doc = ingest(ws, MINI, "inv")
    idx = [b["index"] for b in doc["blocks"]]
    assert idx == list(range(len(idx)))  # 索引连续
    pairs = [(b["line_start"], b["line_end"]) for b in doc["blocks"]]
    assert all(a <= b for a, b in pairs)  # 行号区间合法


# ---------- 中式剧本格式（spec §4.1 增补） ----------

ZH_SAMPLE = "samples/zh_screenplay_sample.txt"


def test_zh_screenplay_structure(ws):
    _, doc = ingest(ws, ZH_SAMPLE, "zh1")
    kinds = Counter(b["kind"] for b in doc["blocks"])
    assert kinds["scene_heading"] == 3
    assert kinds["heading"] == 1 and doc["blocks"]
    heads = [b for b in doc["blocks"] if b["kind"] == "scene_heading"]
    assert heads[0]["scene_no"] == "1-1"
    assert "旧码头仓库" in heads[0]["text"]
    assert heads[0]["declared_chars"] == ["沈舟", "林晚"]
    assert any(w["code"] == "FORMAT_NOTE" for w in doc["warnings"])


def test_zh_dialogue_and_roster(ws):
    _, doc = ingest(ws, ZH_SAMPLE, "zh2")
    chars = Counter(b["text"] for b in doc["blocks"] if b["kind"] == "character")
    assert set(chars) == {"沈舟", "林晚", "老耿"}  # 「卡车司机（老耿）」括注真名入名册
    dlg = [b["text"] for b in doc["blocks"] if b["kind"] == "dialogue"]
    # CJK 字间空格折叠 + 折行并入（句末标点闸）
    assert dlg[0] == "货已经到了，你人呢。"
    assert any(t == "你答应过我，这是最后一次。" for t in dlg)
    # 「林晚把手机扣在桌上。」是动作行，不得并入对白
    assert not any("手机" in t for t in dlg)


def test_zh_page_furniture_stripped(ws):
    _, doc = ingest(ws, ZH_SAMPLE, "zh3")
    texts = [b["text"] for b in doc["blocks"]]
    assert not any(re.match(r"^第\s*\d+\s*页", t) for t in texts)
    assert not any(t.startswith("页") for t in texts)          # 页眉折行残留已剥离
    assert sum("《样例剧》" in t for t in texts) == 1           # 重复书名行只留首次


def test_zh_no_false_positive_on_prose(tmp_path):
    f = tmp_path / "prose.txt"
    f.write_text("景时：一个比喻。\n\n他走了。\n\n她说：再见。\n", encoding="utf-8")
    doc = parse_document(f)  # 结构标记 <3，保持段落路径
    assert all(b["kind"] == "paragraph" for b in doc["blocks"])
    assert not any(w["code"] == "FORMAT_NOTE" for w in doc["warnings"])


def test_zh_compat_ideographs(tmp_path):
    # 康熙部首兼容字形（⽂⾼⼼⾊⼈ 等）必须映射回标准汉字，否则名册与对白识别失灵
    f = tmp_path / "compat.txt"
    f.write_text(
        "第01集\n\n1—1\n\n景时：诊所、日、内\n\n⼈物：陈⽂默 ⾼远\n\n"
        "陈⽂默（OS）：这是⼀个噩梦。\n\n⾼远：我们是⾼中同学。\n\n"
        "1—2\n\n景时：走廊、日、内\n\n⼈物：陈⽂默\n\n陈⽂默：⽆法摆脱。\n",
        encoding="utf-8")
    doc = parse_document(f)
    chars = {b["text"] for b in doc["blocks"] if b["kind"] == "character"}
    assert chars == {"陈文默", "高远"}  # 标准码位
    assert sum(1 for b in doc["blocks"] if b["kind"] == "dialogue") == 3
    assert sum(1 for b in doc["blocks"] if b["kind"] == "scene_heading") == 2
