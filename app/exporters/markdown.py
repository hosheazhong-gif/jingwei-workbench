from __future__ import annotations

from collections.abc import Mapping, Sequence


class MarkdownInternalDraftExporter:
    """把已纳入本版的段落投影写成可编辑 Markdown，不产生新事实。"""

    key = "markdown"
    media_type = "text/markdown; charset=utf-8"
    filename_suffix = ".md"

    def export(self, approved_blocks: Sequence[Mapping[str, object]]) -> bytes:
        header = approved_blocks[0] if approved_blocks else {}
        project_name = str(header.get("project_name") or "内部稿")
        lines = [
            f"# {project_name}",
            "",
            "本文是内部评审初稿的可编辑导出。文字来自当前内部稿，没有重新生成事实，也没有改变主张核验状态。",
            "",
        ]
        override_line = _project_override_line(header)
        if override_line:
            lines.append(override_line)
            lines.append("")

        sections = 0
        for block in approved_blocks:
            title = block.get("title")
            body = block.get("current_text")
            if not title or body is None:
                continue
            sections += 1
            lines.append(f"## {title}")
            lines.append("")
            lines.append(str(body).rstrip())
            lines.append("")
            restriction = block.get("restriction")
            if restriction:
                lines.append(f"写入限制：{restriction}")
                lines.append("")
            treatment_bits = [
                label
                for label in (block.get("review_label"), block.get("override_label"))
                if label
            ]
            if treatment_bits:
                lines.append(
                    "本段处理：" + "；".join(str(bit) for bit in treatment_bits) + "。"
                )
                lines.append("")
            # 来源只给名称和链接／文件名，正文才是主体。
            sources = list(block.get("sources") or [])
            if sources:
                lines.append("来源：")
                for source in sources:
                    title = str(source.get("title") or "未命名材料").strip()
                    locator = str(source.get("locator") or "").strip()
                    note = str(source.get("note") or "").strip()
                    lines.append(
                        "- "
                        + title
                        + (f"（{locator}）" if locator else "")
                        + (f" · {note}" if note else "")
                    )
                lines.append("")
            claims: list = []
            if claims:
                lines.append("口径与来源：")
                for claim in claims:
                    lines.append("- " + _claim_line(claim))
                lines.append("")

        if sections == 0:
            lines.append("本版没有纳入任何段落。")
            lines.append("")

        omitted = list(header.get("omitted_titles") or [])
        if omitted:
            lines.append("## 未进入本版的段落")
            lines.append("")
            for title in omitted:
                lines.append(f"- {title}（从本版排除）")
            lines.append("")

        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


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
