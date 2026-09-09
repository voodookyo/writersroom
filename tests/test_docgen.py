"""docgen 测试：生成后经 validate 全部 ok（含 PAGE 域与 eastAsia 检查）；最小 spec 不编造元数据。"""
import json
import zipfile
from pathlib import Path

import pytest

from writersroom import docgen
from writersroom.core import WritersRoomError

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SPEC = json.loads((SAMPLES / "docgen_spec.json").read_text(encoding="utf-8"))


def test_generate_then_validate_all_ok(tmp_path):
    out = tmp_path / "report.docx"
    result = docgen.generate(SPEC, out)
    assert result["ok"] and out.exists()
    assert result["counts"]["tables"] == 1

    v = docgen.validate(out, SPEC)
    assert v["ok"], v
    by_name = {c["name"]: c for c in v["checks"]}
    assert by_name["footer_page_field"]["ok"]
    assert by_name["eastasia_font"]["ok"]
    assert by_name["tables"]["ok"]
    assert by_name["table_0_widths"]["ok"]
    assert by_name["cover_title"]["ok"]
    assert by_name["header_text"]["ok"]


def test_validate_checks_content_details(tmp_path):
    out = tmp_path / "report.docx"
    docgen.generate(SPEC, out)
    v = docgen.validate(out, SPEC)
    by_name = {c["name"]: c for c in v["checks"]}
    assert "4.0" in by_name["table_0_widths"]["detail"]  # 固定列宽读回
    assert "节数=1" in by_name["sections"]["detail"]
    # 页脚 XML 里确实是 fldChar/instrText 域而非纯文本
    with zipfile.ZipFile(out) as z:
        footer = "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.startswith("word/footer"))
    assert "fldChar" in footer and "PAGE" in footer


def test_minimal_spec_no_invented_metadata(tmp_path):
    spec = {
        "sections": [
            {"heading": "一、概述", "content": [{"type": "paragraph", "text": "合成样例正文。"}]},
        ],
    }
    out = tmp_path / "min.docx"
    result = docgen.generate(spec, out)
    assert result["ok"]
    v = docgen.validate(out, spec)
    assert v["ok"], v

    import docx as _docx
    document = _docx.Document(str(out))
    texts = [p.text for p in document.paragraphs]
    assert "一、概述" in texts
    assert "合成样例正文。" in texts
    # 无 cover → 不编造封面标题；无 font_cjk → eastAsia 检查降级为存在性
    assert all("报告" not in t for t in texts)


def test_table_without_widths_uses_equal_split(tmp_path):
    spec = {
        "font_cjk": "黑体",
        "sections": [
            {"heading": "表", "content": [
                {"type": "table", "headers": ["甲", "乙"], "rows": [["1", "2"]]},
            ]},
        ],
    }
    out = tmp_path / "t.docx"
    docgen.generate(spec, out)
    v = docgen.validate(out, spec)
    assert v["ok"], v
    by_name = {c["name"]: c for c in v["checks"]}
    assert by_name["table_0_widths"]["ok"]


def test_validate_missing_file(tmp_path):
    v = docgen.validate(tmp_path / "nope.docx")
    assert not v["ok"]
    assert v["checks"][0]["name"] == "file_exists"


def test_bad_content_type_errors(tmp_path):
    with pytest.raises(WritersRoomError) as exc:
        docgen.generate({"sections": [{"heading": "x", "content": [{"type": "image"}]}]},
                        tmp_path / "x.docx")
    assert exc.value.hint
