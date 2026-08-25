from __future__ import annotations

import json
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.source_snapshot import snapshot_capabilities
from app.projections.brief import _short_label

_AVAILABILITY = {
    "available": "可访问",
    "path_expired": "文件路径已失效",
    "permission_denied": "无权限",
    "deleted": "已删除",
}


def build_source_list_projection(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    """来源清单：自然语言标题与可访问状态，供补料入口使用。"""
    if not repository.has_project(project_id):
        raise KeyError(f"项目 {project_id} 不存在")
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.title, s.availability, s.supersedes_source_id, s.limitation,
                   s.snapshot_path, s.kind, s.original_url, s.research_question_id,
                   q.question AS question_label, q.label AS question_label_short
            FROM sources s
            LEFT JOIN research_questions q ON q.id = s.research_question_id
            WHERE s.project_id = ?
            ORDER BY s.rowid
            """,
            (project_id,),
        ).fetchall()
        excerpt_rows = connection.execute(
            """
            SELECT e.source_id, e.excerpt, e.locator_json
            FROM evidence_excerpts e
            JOIN sources s ON s.id = e.source_id
            WHERE s.project_id = ?
            ORDER BY e.rowid
            """,
            (project_id,),
        ).fetchall()
    titles = {str(row["id"]): row["title"] for row in rows}
    replaced = {
        str(row["supersedes_source_id"])
        for row in rows
        if row["supersedes_source_id"]
    }
    excerpts: dict[str, list[dict[str, Any]]] = {}
    for row in excerpt_rows:
        text = str(row["excerpt"] or "").strip()
        if not text:
            continue
        excerpts.setdefault(str(row["source_id"]), []).append(
            {
                "text": text,
                "locator": _locator_label(row["locator_json"]),
            }
        )
    return {
        "project_id": project_id,
        "sources": [
            {
                "id": row["id"],
                "title": row["title"],
                "availability": row["availability"],
                "availability_label": _AVAILABILITY.get(
                    row["availability"], row["availability"]
                ),
                "limitation": (row["limitation"] or "").strip() or None,
                "superseded": str(row["id"]) in replaced,
                "supersedes_source_id": row["supersedes_source_id"],
                "supersedes_title": titles.get(str(row["supersedes_source_id"]))
                if row["supersedes_source_id"]
                else None,
                "excerpts": excerpts.get(str(row["id"]), []),
                "research_question_id": row["research_question_id"],
                "question_label": (row["question_label"] or "").strip() or None,
                "question_short_label": _short_label(
                    row["question_label_short"], row["question_label"]
                ),
                "original_url": (row["original_url"] or "").strip() or None,
                **snapshot_capabilities(repository, dict(row)),
            }
            for row in rows
        ],
    }


def _locator_label(raw: str | None) -> str | None:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    label = str(data.get("region_label") or data.get("note") or "").strip()
    kind = str(data.get("kind") or "").strip()
    if kind == "snapshot" and not label:
        return "快照摘录"
    return label or None
