"""详细版导出：把一节稿子背后的东西一次摊开，并且排得能读。

经理版（word / markdown）读起来是一份整理稿，来源只留名称和链接。详细版是另一
件事：正文之外，把每条主张、主张背后的原文摘录、来源标记、机械检查结果、这一节
是第几轮第几版，全部按论文式层级列出来，供内部核对。

排版上有三条规矩：
1. 层级用真正的 Word 标题样式（带大纲级别），导航窗格能用，能一眼看清结构；
2. 注释、口径、来源这类附注是灰色小字，不跟正文抢地方；
3. 粘进来的材料原文整块缩进、左边一条竖线，它自带的小标题不进大纲，
   自带的表格还原成真表格——因为那是材料，不是我的结论。

两个版本读的是同一份 build_approved_export_projection，不产生第二套结论。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.exporters import docx
from app.exporters.layout import line_kind, structure_lines
from app.projections.paragraphs import paragraph_spans

_CN_DIGITS = "〇一二三四五六七八九"
_CHECK_LABELS = [
    ("unsourced_numbers", "正文里没有摘录兜底的数字"),
    ("unsourced_orgs", "正文里没有摘录兜底的机构名"),
    ("novel_claims", "摘录里找不到出处的新说法"),
    ("client_as_verified", "客户口径被当成已核实"),
    ("macro_as_demand", "宏观数据被当成项目需求"),
]
_STATUS_LABELS = {
    "captured": "已捕获，还没核验",
    "source_checked": "已回到来源看过",
    "corroborated": "有旁证",
    "conflicted": "有冲突",
    "stale": "可能过时",
    "unverifiable": "核不了",
    "excluded": "已排除",
}
_INTRO = (
    "这是详细版。除了正文，每一节还列出支撑这节的主张、主张背后的原文摘录、"
    "来源与限制、机械检查结果，以及这一节是第几轮第几版。灰色缩进、左边带竖线的"
    "整块是材料原文，逐字照录，它自带的小标题和表格按原样还原，不进本文的目录。"
    "文字来自当前内部稿，没有重新生成事实，也没有改变任何主张的核验状态。"
)
_SOURCE_HEAD_CHARS = 22

_WORD_STYLES = {
    "title": "Title",
    "subtitle": "Subtitle",
    "lead": "Lead",
    "stamp": "Stamp",
    "p": "BodyText",
    "note": "NoteText",
    "field": "FieldItem",
    "li": "ListItem",
    "qh": "QuoteHead",
    "quote": "QuoteText",
    "qli": "QuoteItem",
    "caption": "Caption",
}


class MarkdownDetailedExporter:
    """详细版 Markdown。"""

    key = "markdown_detailed"
    media_type = "text/markdown; charset=utf-8"
    filename_suffix = ".详细版.md"

    def export(self, approved_blocks: Sequence[Mapping[str, object]]) -> bytes:
        lines: list[str] = []
        previous = ""
        for node in _outline(approved_blocks):
            kind = node["kind"]
            if previous in {"li", "field", "qli"} and kind != previous:
                lines.append("")
            previous = kind
            if kind == "table":
                lines.extend(_markdown_table(node["rows"]))
                lines.append("")
                continue
            text = str(node.get("text") or "")
            if kind == "title":
                lines += ["# " + text, ""]
            elif kind == "h":
                lines += ["#" * min(6, int(node["level"]) + 1) + " " + text, ""]
            elif kind in {"li", "field"}:
                lines.append("- " + text)
            elif kind in {"note", "stamp", "subtitle", "caption"}:
                lines += ["*" + text + "*", ""]
            elif kind == "qh":
                lines += ["> **" + text + "**", ">"]
            elif kind == "quote":
                lines += ["> " + text, ">"]
            elif kind == "qli":
                lines.append("> - " + text)
            else:
                lines += [text, ""]
        body = "\n".join(lines).rstrip() + "\n"
        return body.encode("utf-8")


class WordDetailedExporter:
    """详细版 Word；与经理版共用同一套样式表。"""

    key = "word_detailed"
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    filename_suffix = ".详细版.docx"
    binary = True

    def export(self, approved_blocks: Sequence[Mapping[str, object]]) -> bytes:
        return docx.build_docx(_word_parts(_outline(approved_blocks)))


def _word_parts(nodes: Sequence[Mapping[str, Any]]) -> list[str]:
    parts: list[str] = []
    for node in nodes:
        kind = node["kind"]
        if kind == "table":
            parts.append(docx.table(node["rows"]))
            continue
        if kind == "h":
            style = "Heading" + str(min(4, max(1, int(node["level"]))))
        else:
            style = _WORD_STYLES.get(kind, "BodyText")
        parts.append(
            docx.paragraph(
                str(node.get("text") or ""),
                style=style,
                marks=node.get("marks"),
            )
        )
    return parts


def _markdown_table(rows: Sequence[Sequence[str]]) -> list[str]:
    if not rows:
        return []
    width = max(len(row) for row in rows)
    def line(cells: Sequence[str]) -> str:
        padded = list(cells) + [""] * (width - len(cells))
        return "| " + " | ".join(cell.replace("|", "｜") for cell in padded) + " |"
    out = [line(rows[0]), "|" + "|".join([" --- "] * width) + "|"]
    out.extend(line(row) for row in rows[1:])
    return out


def _outline(approved_blocks: Sequence[Mapping[str, object]]) -> list[dict[str, Any]]:
    """一份详细版的骨架：论文式层级，一层一层往下摊。"""
    header = approved_blocks[0] if approved_blocks else {}
    nodes: list[dict[str, Any]] = [
        _n("title", str(header.get("project_name") or "内部稿") + "　详细版"),
        _n("subtitle", "内部核对用　·　不是给客户的交付物"),
        _n("lead", _INTRO),
    ]
    override_line = _project_override_line(header)
    if override_line:
        nodes.append(_n("note", override_line))

    numbered = [
        block
        for block in approved_blocks
        if block.get("title") and block.get("current_text") is not None
    ]
    if not numbered:
        nodes.append(_n("p", "本版没有纳入任何段落。"))
        return nodes

    nodes.append(_h("目录", 1))
    for index, block in enumerate(numbered, start=1):
        nodes.append(_n("li", _cn(index) + "、" + str(block.get("title"))))

    for index, block in enumerate(numbered, start=1):
        nodes.extend(_section(index, block))

    omitted = list(header.get("omitted_titles") or [])
    if omitted:
        nodes.append(_h("附：未进入本版的段落", 1))
        for title in omitted:
            nodes.append(_n("li", str(title) + "（从本版排除）"))
    return nodes


def _section(index: int, block: Mapping[str, object]) -> list[dict[str, Any]]:
    nodes = [_h(_cn(index) + "、" + str(block.get("title")), 1)]

    stamp = []
    if block.get("round_label"):
        stamp.append(str(block["round_label"]))
    if block.get("current_version"):
        stamp.append("第 " + str(block["current_version"]) + " 版")
    stamp.extend(
        str(label)
        for label in (block.get("review_label"), block.get("override_label"))
        if label
    )
    if stamp:
        nodes.append(_n("stamp", "　·　".join(stamp)))

    counter = _Counter()
    nodes.append(_h(counter.next("正文"), 2))
    nodes.extend(_marked_body(block))
    if block.get("restriction"):
        nodes.append(_n("note", "写入限制：" + str(block["restriction"])))

    nodes.extend(_claim_nodes(block, counter))
    nodes.extend(_source_nodes(block, counter))
    nodes.extend(_judgement_nodes(block, counter))
    nodes.extend(_check_nodes(block, counter))
    return nodes


class _Counter:
    """小节编号。没有内容的小节直接不出现，编号也不留空号。"""

    def __init__(self) -> None:
        self._index = 0

    def next(self, title: str) -> str:
        self._index += 1
        return "（" + _cn(self._index) + "）" + title


def _marked_body(block: Mapping[str, object]) -> list[dict[str, Any]]:
    """正文按行排版，同时保住标红的位置。"""
    body = str(block.get("current_text") or "")
    marks = list(block.get("unsourced_marks") or [])
    nodes: list[dict[str, Any]] = []
    for line, start in paragraph_spans(body):
        kind = line_kind(line)
        shifted = [
            {
                "text": mark["text"],
                "start": mark["start"] - start,
                "end": mark["end"] - start,
            }
            for mark in marks
            if mark["start"] >= start and mark["end"] <= start + len(line)
        ]
        if kind == "heading":
            nodes.append(_h(line.strip(), 3))
        elif kind == "item":
            nodes.append({"kind": "li", "text": line.strip(), "marks": shifted})
        elif kind == "caption":
            nodes.append(_n("caption", line.strip()))
        else:
            nodes.append({"kind": "p", "text": line, "marks": shifted})
    return nodes


def _claim_nodes(
    block: Mapping[str, object], counter: "_Counter"
) -> list[dict[str, Any]]:
    claims = list(block.get("claims") or [])
    nodes = [_h(counter.next("本节支撑的主张与原文"), 2)]
    if not claims:
        nodes.append(_n("p", "这一节还没有挂上任何主张。"))
        return nodes
    for order, claim in enumerate(claims, start=1):
        source_title = str(claim.get("source_title") or "").strip()
        head = "主张 " + str(order)
        if source_title:
            head += "　出自《" + _clip(source_title, _SOURCE_HEAD_CHARS) + "》"
        nodes.append(_h(head, 3))
        nodes.extend(_material_nodes(str(claim.get("text") or "").strip()))

        marks = []
        if claim.get("provenance_scope") == "client_provided":
            marks.append("客户提供")
        if claim.get("provenance_scope") == "manager_feedback":
            marks.append("经理反馈")
        if claim.get("independently_verified") is False:
            marks.append("未独立核实")
        status = str(claim.get("verification_status") or "")
        if status:
            marks.append("核验状态：" + _STATUS_LABELS.get(status, status))
        if marks:
            nodes.append(_n("field", "口径：" + "；".join(marks)))
        if claim.get("delivery_rule"):
            nodes.append(_n("field", "写法约束：" + str(claim["delivery_rule"])))
        if source_title:
            locator = str(
                claim.get("source_url") or claim.get("source_file") or ""
            ).strip()
            nodes.append(
                _n("field", "来源：" + source_title + (f"（{locator}）" if locator else ""))
            )
        claim_text = str(claim.get("text") or "").strip()
        evidence = list(claim.get("evidence") or [])
        if not evidence:
            nodes.append(_n("field", "原文摘录：这条主张下面还没有挂摘录。"))
            continue
        for spot, piece in enumerate(evidence, start=1):
            where = str(piece.get("locator") or "").strip()
            body = str(piece.get("text") or "").strip()
            head = "原文摘录 " + str(spot) + (f"（{where}）" if where else "")
            if body == claim_text:
                nodes.append(_n("field", head + "：与上面这条同文，逐字来自材料。"))
            else:
                nodes.append(_n("field", head + "："))
                nodes.extend(_material_nodes(body))
            if piece.get("context_limit"):
                nodes.append(_n("field", "摘录的上下文限制：" + str(piece["context_limit"])))
    return nodes


def _material_nodes(text: str) -> list[dict[str, Any]]:
    """材料原文：认出它本来的层级，整块缩进，不进本文的目录。"""
    nodes: list[dict[str, Any]] = []
    for piece in structure_lines(text):
        kind = piece["kind"]
        if kind == "table":
            nodes.append({"kind": "table", "rows": piece["rows"]})
        elif kind == "heading":
            nodes.append(_n("qh", piece["text"]))
        elif kind == "item":
            nodes.append(_n("qli", piece["text"]))
        elif kind == "caption":
            nodes.append(_n("caption", piece["text"]))
        else:
            nodes.append(_n("quote", piece["text"]))
    return nodes


def _source_nodes(
    block: Mapping[str, object], counter: "_Counter"
) -> list[dict[str, Any]]:
    sources = list(block.get("sources") or [])
    nodes = [_h(counter.next("本节来源清单"), 2)]
    if not sources:
        nodes.append(_n("p", "这一节还没有来源。"))
        return nodes
    for source in sources:
        title = str(source.get("title") or "未命名材料").strip()
        locator = str(source.get("locator") or "").strip()
        note = str(source.get("note") or "").strip()
        nodes.append(_n("li", title + (f"（{locator}）" if locator else "")))
        tail = []
        if note:
            tail.append(note)
        if source.get("limitation"):
            tail.append("限制：" + str(source["limitation"]))
        if tail:
            nodes.append(_n("field", "　".join(tail)))
    return nodes


def _judgement_nodes(
    block: Mapping[str, object], counter: "_Counter"
) -> list[dict[str, Any]]:
    findings = list(block.get("findings") or [])
    options = list(block.get("options") or [])
    if not findings and not options:
        return []
    nodes = [_h(counter.next("本节的总判断与可试方向"), 2)]
    for finding in findings:
        label = str(finding.get("confidence_label") or "").strip()
        nodes.append(
            _n(
                "li",
                "总判断：" + str(finding.get("text") or "").strip()
                + (f"（把握 {label}）" if label else ""),
            )
        )
    for option in options:
        label = str(option.get("status_label") or "").strip()
        nodes.append(
            _n(
                "li",
                "可试方向：" + str(option.get("text") or "").strip()
                + (f"（{label}）" if label else ""),
            )
        )
    return nodes


def _check_nodes(
    block: Mapping[str, object], counter: "_Counter"
) -> list[dict[str, Any]]:
    checks = dict(block.get("checks") or {})
    nodes = [_h(counter.next("机械检查"), 2)]
    if not checks:
        nodes.append(_n("p", "这一节没有跑机械检查。"))
        return nodes
    hit = False
    for key, label in _CHECK_LABELS:
        items = list(checks.get(key) or [])
        if not items:
            continue
        hit = True
        quoted = "、".join(
            "「" + str(item.get("text") or "").strip() + "」" for item in items[:8]
        )
        more = "" if len(items) <= 8 else f"，另有 {len(items) - 8} 处"
        nodes.append(_n("li", f"{label}：{len(items)} 处 —— {quoted}{more}"))
    if checks.get("stale"):
        hit = True
        nodes.append(_n("li", "有材料已被替代或已过期，这一节需要重看。"))
    if not hit:
        nodes.append(_n("p", "这一节没有触发机械检查。"))
    else:
        nodes.append(
            _n("note", "机械检查只标出可疑的地方，不改任何主张的核验状态，也不代表结论错。")
        )
    return nodes


def _project_override_line(header: Mapping[str, object]) -> str:
    label = header.get("project_override_label")
    reason = header.get("project_override_reason")
    if not label and not reason:
        return ""
    parts = [f"项目级处理：{label or '带风险推进'}"]
    if reason:
        parts.append(str(reason).rstrip("。"))
    parts.append("证据核验状态未改变")
    return "。".join(parts) + "。"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _cn(number: int) -> str:
    if number <= 0:
        return str(number)
    if number < 10:
        return _CN_DIGITS[number]
    if number < 20:
        return "十" + ("" if number == 10 else _CN_DIGITS[number - 10])
    if number < 100:
        tens, ones = divmod(number, 10)
        return _CN_DIGITS[tens] + "十" + ("" if ones == 0 else _CN_DIGITS[ones])
    return str(number)


def _h(text: str, level: int) -> dict[str, Any]:
    return {"kind": "h", "text": text, "level": level}


def _n(kind: str, text: str) -> dict[str, Any]:
    return {"kind": kind, "text": text}
