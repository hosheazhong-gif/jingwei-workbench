"""交付导出器注册表。新增格式只在此注册，不修改 Markdown 导出语义。"""

from __future__ import annotations

from app.exporters.detailed import MarkdownDetailedExporter, WordDetailedExporter
from app.exporters.markdown import MarkdownInternalDraftExporter
from app.exporters.plain_text import PlainTextReviewExporter
from app.exporters.word import WordInternalDraftExporter
from app.ports.contracts import DeliverableExporter

_DEFAULT_EXPORTERS: dict[str, DeliverableExporter] = {
    MarkdownInternalDraftExporter.key: MarkdownInternalDraftExporter(),
    MarkdownDetailedExporter.key: MarkdownDetailedExporter(),
    PlainTextReviewExporter.key: PlainTextReviewExporter(),
    WordInternalDraftExporter.key: WordInternalDraftExporter(),
    WordDetailedExporter.key: WordDetailedExporter(),
}


def default_exporters() -> dict[str, DeliverableExporter]:
    return dict(_DEFAULT_EXPORTERS)
