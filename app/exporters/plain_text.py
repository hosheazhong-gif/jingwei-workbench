from __future__ import annotations

from collections.abc import Mapping, Sequence


class PlainTextReviewExporter:
    """同一批准投影的纯文本内部评审包；不改核验状态，不接触模板。"""

    key = "plain_text"
    media_type = "text/plain; charset=utf-8"
    filename_suffix = ".txt"

    def export(self, approved_blocks: Sequence[Mapping[str, object]]) -> bytes:
        header = approved_blocks[0] if approved_blocks else {}
        project_name = str(header.get("project_name") or "内部稿")
        lines = [
            project_name,
            "内部评审包",
            "",
            "文字来自当前内部稿，没有重新生成事实，也没有改变主张核验状态。",
            "",
        ]
        override_label = header.get("project_override_label")
        override_reason = header.get("project_override_reason")
        if override_label or override_reason:
            parts = [str(override_label or "带风险推进")]
            if override_reason:
                parts.append(str(override_reason).rstrip("。"))
            lines.append("项目级处理：" + "。".join(parts) + "。证据核验状态未改变。")
            lines.append("")

        pending_titles: list[str] = []
        sections = 0
        for block in approved_blocks:
            title = block.get("title")
            body = block.get("current_text")
            if not title or body is None:
                continue
            sections += 1
            lines.append(str(title))
            lines.append(str(body).rstrip())
            restriction = block.get("restriction")
            if restriction:
                lines.append("写入限制：" + str(restriction))
            sources = _source_names(block)
            if sources:
                lines.append("来源：" + "；".join(sources))
            if _needs_rereview(block):
                pending_titles.append(str(title))
                lines.append("待重审：是")
            lines.append("")

        if sections == 0:
            lines.append("本版没有纳入任何段落。")
            lines.append("")
        if pending_titles:
            lines.append("待重审段落：")
            for title in pending_titles:
                lines.append("- " + title)
            lines.append("")
        omitted = list(header.get("omitted_titles") or [])
        if omitted:
            lines.append("未进入本版的段落：")
            for title in omitted:
                lines.append("- " + str(title))
            lines.append("")
        return ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def _source_names(block: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for claim in list(block.get("claims") or []):
        if not isinstance(claim, Mapping):
            continue
        title = claim.get("source_title")
        if not title:
            continue
        name = str(title)
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def _needs_rereview(block: Mapping[str, object]) -> bool:
    action = block.get("review_action")
    return action in (None, "modify")
