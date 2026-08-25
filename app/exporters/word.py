from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.exporters import docx
from app.exporters.layout import line_kind
from app.projections.paragraphs import paragraph_spans


class WordInternalDraftExporter:
    """同一批准投影的可编辑 Word；不产生新事实，不改核验状态。"""

    key = "word"
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    filename_suffix = ".docx"
    binary = True

    def export(self, approved_blocks: Sequence[Mapping[str, object]]) -> bytes:
        return docx.build_docx(_paragraphs(approved_blocks))


def docx_bytes(paragraphs: Sequence[str]) -> bytes:
    """老接缝：把已经写好的块打包成 .docx。"""
    return docx.build_docx(paragraphs)


def _paragraphs(approved_blocks: Sequence[Mapping[str, object]]) -> list[str]:
    header = approved_blocks[0] if approved_blocks else {}
    project_name = str(header.get("project_name") or "内部稿")
    parts = [
        docx.paragraph(project_name, style="Title"),
        docx.paragraph("给经理的整理稿　·　内部用", style="Subtitle"),
        docx.paragraph(
            "本文是内部评审初稿的可编辑导出。文字来自当前内部稿，"
            "没有重新生成事实，也没有改变主张核验状态。",
            style="Lead",
        ),
    ]
    override_line = _project_override_line(header)
    if override_line:
        parts.append(docx.paragraph(override_line, style="NoteText"))

    sections = 0
    for block in approved_blocks:
        title = block.get("title")
        body = block.get("current_text")
        if not title or body is None:
            continue
        sections += 1
        parts.append(docx.paragraph(str(title), style="Heading1"))
        parts.extend(_body_parts(str(body), block.get("unsourced_marks") or []))
        restriction = block.get("restriction")
        if restriction:
            # 附注是小字：它是给写稿的人看的约束，不该跟正文抢地方
            parts.append(
                docx.paragraph("写入限制：" + str(restriction), style="NoteText")
            )
        treatment_bits = [
            label
            for label in (block.get("review_label"), block.get("override_label"))
            if label
        ]
        if treatment_bits:
            parts.append(
                docx.paragraph(
                    "本段处理：" + "；".join(str(bit) for bit in treatment_bits) + "。",
                    style="NoteText",
                )
            )
        # 来源只给名称和链接／文件名，正文才是主体；主张全文留在账本里。
        sources = list(block.get("sources") or [])
        if sources:
            parts.append(docx.paragraph("来源：", style="Label"))
            for source in sources:
                parts.append(
                    docx.paragraph("- " + _source_line(source), style="ListItem")
                )

    if sections == 0:
        parts.append(docx.paragraph("本版没有纳入任何段落。"))

    omitted = list(header.get("omitted_titles") or [])
    if omitted:
        parts.append(docx.paragraph("未进入本版的段落", style="Heading1"))
        for title in omitted:
            parts.append(
                docx.paragraph("- " + str(title) + "（从本版排除）", style="ListItem")
            )
    return parts


def _body_parts(body: str, marks: list) -> list[str]:
    """正文按行判类型：小标题给标题样式（导航窗格能用），分条给悬挂缩进。

    标红位置按原文偏移算，判类型不改一个字。
    """
    parts: list[str] = []
    for line, start in paragraph_spans(body):
        line_marks = [
            {
                "text": item["text"],
                "start": item["start"] - start,
                "end": item["end"] - start,
            }
            for item in marks
            if item["start"] >= start and item["end"] <= start + len(line)
        ]
        kind = line_kind(line)
        if kind == "heading":
            parts.append(docx.paragraph(line.strip(), style="Heading2"))
        elif kind == "item":
            parts.append(
                docx.paragraph(line.strip(), style="ListItem", marks=line_marks)
            )
        elif kind == "caption":
            parts.append(docx.paragraph(line.strip(), style="Caption"))
        else:
            parts.append(docx.paragraph(line, style="BodyText", marks=line_marks))
    return parts


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


def _source_line(source: Mapping[str, object]) -> str:
    title = str(source.get("title") or "未命名材料").strip()
    locator = str(source.get("locator") or "").strip()
    note = str(source.get("note") or "").strip()
    line = title + ("（" + locator + "）" if locator else "")
    return line + ("　" + note if note else "")


def _claim_line(claim: Mapping[str, object]) -> str:
    parts = [str(claim.get("text") or "").strip()]
    if claim.get("provenance_scope") == "client_provided":
        parts.append("客户提供")
    if claim.get("independently_verified") is False:
        parts.append("未独立核实")
    delivery_rule = claim.get("delivery_rule")
    if delivery_rule:
        parts.append(str(delivery_rule))
    source_title = claim.get("source_title")
    if source_title:
        parts.append("来源：" + str(source_title))
    return "；".join(part for part in parts if part)
