from __future__ import annotations

import base64
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.exporters import default_exporters
from app.ports.contracts import DeliverableExporter
from app.projections.export import build_approved_export_projection


class ExportError(ValueError):
    pass


def export_project(
    repository: SqliteRepository,
    project_id: str,
    exporter_key: str = "markdown",
    *,
    exporters: dict[str, DeliverableExporter] | None = None,
) -> dict[str, Any]:
    registry = default_exporters() if exporters is None else exporters
    exporter = registry.get(exporter_key)
    if exporter is None:
        raise ExportError(f"未找到导出器 {exporter_key}")

    try:
        projection = build_approved_export_projection(repository, project_id)
    except KeyError as error:
        raise ExportError(str(error)) from error
    statuses_before = _claim_statuses(repository, project_id)
    body = exporter.export(projection["approved_blocks"])
    statuses_after = _claim_statuses(repository, project_id)
    if statuses_before != statuses_after:
        raise ExportError("导出不得改变主张核验状态")

    suffix = getattr(exporter, "filename_suffix", ".md")
    filename = projection["filename"]
    if suffix and not filename.endswith(suffix):
        filename = filename.rsplit(".", 1)[0] + suffix
    media_type = getattr(exporter, "media_type", "text/markdown; charset=utf-8")
    if getattr(exporter, "binary", False):
        content = base64.b64encode(body).decode("ascii")
        encoding = "base64"
    else:
        content = body.decode("utf-8")
        encoding = "utf-8"

    block_ids = [
        block["id"]
        for block in projection["approved_blocks"]
        if block.get("id")
    ]
    omitted_titles = list(projection["omitted_titles"])
    return {
        "exporter_key": exporter.key,
        "filename": filename,
        "media_type": media_type,
        "content_encoding": encoding,
        "content": content,
        "block_ids": block_ids,
        "omitted_titles": omitted_titles,
        "confirmation": {
            "recorded": False,
            "record_kind": "export",
            "exporter_key": exporter.key,
            "filename": filename,
            "block_count": len(block_ids),
            "omitted_titles": omitted_titles,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": (
                f"已导出「{filename}」。未改变主张核验，也未改写内部稿。"
            ),
        },
    }


def _claim_statuses(repository: SqliteRepository, project_id: str) -> dict[str, str]:
    with repository.connect() as connection:
        if connection.execute(
            "SELECT 1 FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone() is None:
            raise ExportError(f"项目 {project_id} 不存在")
        rows = connection.execute(
            """
            SELECT id, verification_status FROM claims
            WHERE project_id = ? ORDER BY rowid
            """,
            (project_id,),
        )
        return {row["id"]: row["verification_status"] for row in rows}
