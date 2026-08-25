from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol


class ProjectTemplate(Protocol):
    key: str

    def natural_language_labels(self) -> Mapping[str, str]: ...


class AnalysisModule(Protocol):
    key: str

    def recommended_question_labels(self) -> Sequence[str]: ...


class SourceAdapter(Protocol):
    key: str

    def capture(self, source: Path, project_files: Path) -> Mapping[str, Any]: ...


class WebSearchAdapter(Protocol):
    key: str

    def search(self, query: str) -> Sequence[Mapping[str, Any]]: ...


class Parser(Protocol):
    key: str

    def parse(self, captured_source: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...


class ExecutionStrategy(Protocol):
    key: str

    def run(self, project_id: str) -> None: ...


class DeliverableExporter(Protocol):
    key: str

    def export(self, approved_blocks: Sequence[Mapping[str, Any]]) -> bytes: ...


class ViewProjection(Protocol):
    key: str

    def render(self, project_id: str) -> Mapping[str, Any]: ...
