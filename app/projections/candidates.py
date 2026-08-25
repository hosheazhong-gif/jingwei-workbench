from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.brief import _short_label

_STATUS_LABELS = {
    "captured": "已收录，尚未打开",
    "opened": "已打开，尚未升为来源",
    "promoted": "已升为可引用来源",
    "discarded": "已排除",
}


def build_candidate_source_projection(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    """网页候选列表。未打开的链接不是可引用 Source。"""
    if not repository.has_project(project_id):
        raise KeyError(f"项目 {project_id} 不存在")
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT c.id, c.url, c.title, c.note, c.status, c.opened_at,
                   c.promoted_source_id, c.research_question_id,
                   q.question AS question_label, q.label AS question_label_short
            FROM candidate_sources c
            LEFT JOIN research_questions q ON q.id = c.research_question_id
            WHERE c.project_id = ?
            ORDER BY c.rowid
            """,
            (project_id,),
        ).fetchall()
    return {
        "project_id": project_id,
        "candidates": [
            {
                "id": row["id"],
                "url": row["url"],
                "title": row["title"],
                "note": row["note"],
                "status": row["status"],
                "status_label": _STATUS_LABELS.get(row["status"], row["status"]),
                "opened_at": row["opened_at"],
                "promoted_source_id": row["promoted_source_id"],
                "research_question_id": row["research_question_id"],
                "question_label": (row["question_label"] or "").strip() or None,
                "question_short_label": _short_label(
                    row["question_label_short"], row["question_label"]
                ),
                "can_open": row["status"] in {"captured", "opened", "promoted"},
                "can_promote": row["status"] == "opened",
                "can_discard": row["status"] in {"captured", "opened"},
            }
            for row in rows
        ],
    }
