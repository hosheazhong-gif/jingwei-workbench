"""一个够用的 .docx 写入器：带真正的样式表。

之前的导出把加粗和字号直接写在每个 run 上，没有 styles.xml。后果是 Word 的
导航窗格永远是空的（导航窗格认的是大纲级别，不是加粗），行距、缩进、字号层级
也全都没有，读起来是一面墙。这里补上：

- docDefaults 定字体、行距（1.5 倍）、段后距；
- 标题 1-4 用内置名 heading 1-4 并带 w:outlineLvl，导航窗格因此能用；
- 正文首行缩进两字，条目用悬挂缩进，注释是灰色小字，摘录整体缩进；
- 粘进来的表格还原成真表格（w:tbl），文字一个不改。

只管排版。不改一个字，也不改任何主张的核验状态。
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from xml.sax.saxutils import escape

_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# 半磅。21 = 10.5 磅，中文正文常用的五号。
_SIZES = {
    "Title": "44",
    "Heading1": "36",
    "Heading2": "30",
    "Heading3": "25",
    "Heading4": "22",
    "BodyText": "21",
    "NoteText": "18",
    "QuoteText": "20",
    "QuoteHead": "21",
    "ListItem": "21",
    "Caption": "18",
    "TableText": "19",
}
_MUTED = "6A655E"
_QUOTE_INK = "3F3A32"
_MARK_INK = "9B2C2C"
_ARCHIVE_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def build_docx(parts: Sequence[str]) -> bytes:
    """把已经写好的块（w:p / w:tbl）打包成 .docx。"""
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_NS}"><w:body>'
        + "".join(parts)
        + _SECTION
        + "</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        _write_archive_file(archive, "[Content_Types].xml", _CONTENT_TYPES)
        _write_archive_file(archive, "_rels/.rels", _RELS)
        _write_archive_file(archive, "word/_rels/document.xml.rels", _DOCUMENT_RELS)
        _write_archive_file(archive, "word/styles.xml", _STYLES)
        _write_archive_file(archive, "word/settings.xml", _SETTINGS)
        _write_archive_file(archive, "word/document.xml", document)
    return buffer.getvalue()


def _write_archive_file(archive: zipfile.ZipFile, name: str, content: str) -> None:
    """写入稳定的 ZIP 元数据，让相同稿件生成完全相同的 DOCX。"""
    entry = zipfile.ZipInfo(name, date_time=_ARCHIVE_TIMESTAMP)
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.create_system = 0
    archive.writestr(entry, content.encode("utf-8"))


def paragraph(
    text: str,
    *,
    style: str = "BodyText",
    marks: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    """一段。marks 是要标红的区间（未挂来源的数字和机构名）。"""
    p_pr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>'
    if not marks:
        return "<w:p>" + p_pr + _run(text) + "</w:p>"
    runs: list[str] = []
    cursor = 0
    for mark in marks:
        start = max(0, int(mark["start"]))
        end = max(start, int(mark["end"]))
        if start > cursor:
            runs.append(_run(text[cursor:start]))
        runs.append(_run(text[start:end], color=_MARK_INK))
        cursor = end
    if cursor < len(text):
        runs.append(_run(text[cursor:]))
    if not runs:
        runs.append(_run(text))
    return "<w:p>" + p_pr + "".join(runs) + "</w:p>"


def table(rows: Sequence[Sequence[str]]) -> str:
    """把制表符分出来的行还原成真表格；单元格文字一字未改。"""
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    if width < 2:
        return ""
    shares = _column_shares(rows, width)
    body = []
    for index, row in enumerate(rows):
        cells = list(row) + [""] * (width - len(row))
        head = index == 0
        row_pr = "<w:trPr><w:tblHeader/></w:trPr>" if head else ""
        body.append(
            "<w:tr>"
            + row_pr
            + "".join(
                "<w:tc>"
                + f'<w:tcPr><w:tcW w:w="{shares[column]}" w:type="pct"/>'
                + ('<w:shd w:val="clear" w:fill="F2EFE4"/>' if head else "")
                + "</w:tcPr>"
                + paragraph(
                    cell, style="TableHead" if head else "TableText"
                )
                + "</w:tc>"
                for column, cell in enumerate(cells)
            )
            + "</w:tr>"
        )
    grid = "".join(
        '<w:gridCol w:w="%d"/>' % int(9000 * share / 5000) for share in shares
    )
    return (
        "<w:tbl><w:tblPr>"
        '<w:tblW w:w="5000" w:type="pct"/>'
        "<w:tblBorders>"
        + "".join(
            f'<w:{edge} w:val="single" w:sz="4" w:space="0" w:color="D8D3C6"/>'
            for edge in ("top", "left", "bottom", "right", "insideH", "insideV")
        )
        + "</w:tblBorders>"
        '<w:tblCellMar><w:top w:w="60" w:type="dxa"/><w:bottom w:w="60" w:type="dxa"/>'
        '<w:left w:w="90" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tblCellMar>'
        "</w:tblPr>"
        f"<w:tblGrid>{grid}</w:tblGrid>"
        + "".join(body)
        + "</w:tbl>"
        # 表格后面紧跟一个空段，否则 Word 里两张表会粘在一起
        + '<w:p><w:pPr><w:pStyle w:val="TableGap"/></w:pPr></w:p>'
    )


def _column_shares(rows: Sequence[Sequence[str]], width: int) -> list[int]:
    """按内容长度分列宽。四列平分会让「重点内容」挤成一条，标题列反而空半格。"""
    weights = []
    for column in range(width):
        longest = max(
            (len(row[column]) for row in rows if column < len(row)), default=1
        )
        weights.append(max(4, min(longest, 60)))
    total = sum(weights) or 1
    shares = [max(600, int(5000 * weight / total)) for weight in weights]
    scale = 5000 / sum(shares)
    shares = [int(share * scale) for share in shares]
    shares[-1] += 5000 - sum(shares)
    return shares


def _run(text: str, *, color: str | None = None) -> str:
    r_pr = f'<w:rPr><w:color w:val="{color}"/></w:rPr>' if color else ""
    return (
        "<w:r>"
        + r_pr
        + '<w:t xml:space="preserve">'
        + escape(text)
        + "</w:t></w:r>"
    )


def _twips(chars_hundredths: int) -> int:
    """把「百分之一个字」换成缇。正文 10.5 磅，一个字 210 缇。"""
    return int(round(chars_hundredths * 2.1))


def _style(
    style_id: str,
    name: str,
    *,
    size: str,
    bold: bool = False,
    color: str | None = None,
    outline: int | None = None,
    before: int = 0,
    after: int = 120,
    line: int = 360,
    first_line_chars: int = 0,
    left_chars: int = 0,
    hanging_chars: int = 0,
    justify: str = "both",
    keep_next: bool = False,
    border_left: bool = False,
) -> str:
    p_pr = ['<w:spacing w:before="%d" w:after="%d" w:line="%d" w:lineRule="auto"/>'
            % (before, after, line)]
    if first_line_chars or left_chars or hanging_chars:
        # *Chars 是百分之一字；缇（twip）要按字宽换算。10.5 磅的字宽 210 缇，
        # 所以「两字」= 200 百分之一字 = 420 缇。早先直接乘 21，缩进大了十倍。
        bits = []
        if left_chars:
            bits.append(f'w:leftChars="{left_chars}" w:left="{_twips(left_chars)}"')
        if hanging_chars:
            bits.append(
                f'w:hangingChars="{hanging_chars}" w:hanging="{_twips(hanging_chars)}"'
            )
        elif first_line_chars:
            bits.append(
                f'w:firstLineChars="{first_line_chars}"'
                f' w:firstLine="{_twips(first_line_chars)}"'
            )
        p_pr.append("<w:ind " + " ".join(bits) + "/>")
    if border_left:
        p_pr.append(
            "<w:pBdr>"
            '<w:left w:val="single" w:sz="12" w:space="8" w:color="D8D3C6"/>'
            "</w:pBdr>"
        )
    if keep_next:
        p_pr.append("<w:keepNext/><w:keepLines/>")
    if outline is not None:
        p_pr.append(f'<w:outlineLvl w:val="{outline}"/>')
    p_pr.append(f'<w:jc w:val="{justify}"/>')
    r_pr = [f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>']
    if bold:
        r_pr.append("<w:b/><w:bCs/>")
    if color:
        r_pr.append(f'<w:color w:val="{color}"/>')
    return (
        f'<w:style w:type="paragraph" w:styleId="{style_id}">'
        f'<w:name w:val="{name}"/>'
        '<w:basedOn w:val="Normal"/><w:qFormat/>'
        "<w:pPr>" + "".join(p_pr) + "</w:pPr>"
        "<w:rPr>" + "".join(r_pr) + "</w:rPr>"
        "</w:style>"
    )


_STYLE_DEFS: Iterable[str] = (
    # 大纲级别 0-3 就是导航窗格里的四层；名字用内置的 heading N，Word 才认。
    _style("Title", "Title", size=_SIZES["Title"], bold=True, after=200,
           justify="center", keep_next=True),
    _style("Subtitle", "Subtitle", size=_SIZES["NoteText"], color=_MUTED,
           after=320, justify="center"),
    _style("Heading1", "heading 1", size=_SIZES["Heading1"], bold=True,
           outline=0, before=400, after=180, keep_next=True, justify="left"),
    _style("Heading2", "heading 2", size=_SIZES["Heading2"], bold=True,
           outline=1, before=300, after=150, keep_next=True, justify="left"),
    _style("Heading3", "heading 3", size=_SIZES["Heading3"], bold=True,
           outline=2, before=240, after=120, keep_next=True, justify="left"),
    _style("Heading4", "heading 4", size=_SIZES["Heading4"], bold=True,
           outline=3, before=200, after=100, keep_next=True, justify="left"),
    _style("BodyText", "Body Text", size=_SIZES["BodyText"], first_line_chars=200),
    _style("Lead", "Lead", size=_SIZES["BodyText"], color=_MUTED, after=200),
    _style("NoteText", "Note", size=_SIZES["NoteText"], color=_MUTED,
           left_chars=200, after=80, line=300),
    _style("Stamp", "Stamp", size=_SIZES["NoteText"], color=_MUTED,
           after=140, line=300, justify="left"),
    _style("ListItem", "List Item", size=_SIZES["ListItem"],
           left_chars=200, hanging_chars=200, after=80),
    _style("FieldItem", "Field Item", size=_SIZES["NoteText"], color=_MUTED,
           left_chars=300, hanging_chars=100, after=40, line=300),
    # 材料原文整块缩进、左边一条竖线，一眼看出这是引用不是我的正文
    _style("QuoteText", "Quote", size=_SIZES["QuoteText"], color=_QUOTE_INK,
           left_chars=200, first_line_chars=200, after=100, line=320,
           border_left=True),
    _style("QuoteHead", "Quote Heading", size=_SIZES["QuoteHead"], bold=True,
           color=_QUOTE_INK, left_chars=200, before=140, after=80, line=320,
           justify="left", keep_next=True, border_left=True),
    _style("QuoteItem", "Quote Item", size=_SIZES["QuoteText"], color=_QUOTE_INK,
           left_chars=400, hanging_chars=200, after=70, line=320,
           border_left=True),
    # 「来源：」这种行内小标签不能用标题样式，否则导航窗格里五个「来源：」并排
    _style("Label", "Label", size=_SIZES["BodyText"], bold=True,
           before=160, after=60, justify="left"),
    _style("Caption", "caption", size=_SIZES["Caption"], color=_MUTED,
           before=60, after=140, justify="center"),
    _style("TableText", "Table Text", size=_SIZES["TableText"], after=0, line=280,
           justify="left"),
    _style("TableHead", "Table Head", size=_SIZES["TableText"], bold=True,
           after=0, line=280, justify="left"),
    _style("TableGap", "Table Gap", size="10", after=120, line=240),
)

_STYLES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:styles xmlns:w="{_NS}">'
    "<w:docDefaults><w:rPrDefault><w:rPr>"
    '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman"'
    ' w:eastAsia="等线" w:cs="Times New Roman"/>'
    f'<w:sz w:val="{_SIZES["BodyText"]}"/><w:szCs w:val="{_SIZES["BodyText"]}"/>'
    "</w:rPr></w:rPrDefault>"
    "<w:pPrDefault><w:pPr>"
    '<w:spacing w:before="0" w:after="120" w:line="360" w:lineRule="auto"/>'
    '<w:jc w:val="both"/>'
    "</w:pPr></w:pPrDefault></w:docDefaults>"
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/><w:qFormat/></w:style>'
    + "".join(_STYLE_DEFS)
    + "</w:styles>"
)

_SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:settings xmlns:w="{_NS}">'
    "<w:zoom w:percent=\"100\"/>"
    "<w:defaultTabStop w:val=\"420\"/>"
    "</w:settings>"
)

# A4，上下 2.54cm、左右 3.18cm 的常规页边距
_SECTION = (
    "<w:sectPr>"
    '<w:pgSz w:w="11906" w:h="16838"/>'
    '<w:pgMar w:top="1440" w:right="1800" w:bottom="1440" w:left="1800"'
    ' w:header="851" w:footer="992" w:gutter="0"/>'
    "</w:sectPr>"
)

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>
</Types>
"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOCUMENT_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>
</Relationships>
"""
