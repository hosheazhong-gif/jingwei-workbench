"""未来模板、适配器、策略与导出器的稳定接缝。"""

from .contracts import (
    AnalysisModule,
    DeliverableExporter,
    ExecutionStrategy,
    Parser,
    ProjectTemplate,
    SourceAdapter,
    ViewProjection,
    WebSearchAdapter,
)
from .draft import ModelDraftAdapter

__all__ = [
    "AnalysisModule",
    "DeliverableExporter",
    "ExecutionStrategy",
    "ModelDraftAdapter",
    "Parser",
    "ProjectTemplate",
    "SourceAdapter",
    "ViewProjection",
    "WebSearchAdapter",
]
