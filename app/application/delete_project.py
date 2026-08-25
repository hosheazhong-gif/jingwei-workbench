from __future__ import annotations

import shutil
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.projects import build_project_list_projection


class ProjectDeleteError(ValueError):
    pass


def delete_project(repository: SqliteRepository, project_id: str) -> dict[str, Any]:
    """删除一个题目及其对象和受控文件，不改写其他题目。"""
    if not repository.has_project(project_id):
        raise ProjectDeleteError(f"项目 {project_id} 不存在")
    with repository.connect() as connection:
        others = {
            row["id"]
            for row in connection.execute(
                "SELECT id FROM projects WHERE id != ?",
                (project_id,),
            )
        }
        other_drafts = _block_texts_except(connection, project_id)
        other_status = _claim_statuses_except(connection, project_id)

    files_dir = repository.files_root / project_id
    with repository.transaction() as connection:
        _delete_project_rows(connection, project_id)

    if files_dir.exists():
        shutil.rmtree(files_dir)

    with repository.connect() as connection:
        remaining = {
            row["id"] for row in connection.execute("SELECT id FROM projects")
        }
        if remaining != others:
            raise ProjectDeleteError("删除不得影响其他题目")
        if _block_texts_except(connection, project_id) != other_drafts:
            raise ProjectDeleteError("删除不得改写其他题目内部稿")
        if _claim_statuses_except(connection, project_id) != other_status:
            raise ProjectDeleteError("删除不得改变其他题目主张核验状态")

    return {
        "deleted": True,
        "project_id": project_id,
        "projects": build_project_list_projection(repository),
        "confirmation": {
            "recorded": True,
            "did_not_overwrite_existing": True,
            "message": "题目已删除。其他题目的来源、内部稿和核验状态未改变。",
        },
    }


def _delete_project_rows(connection: Any, project_id: str) -> None:
    connection.execute(
        "UPDATE sources SET supersedes_source_id = NULL WHERE project_id = ?",
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM claim_evidence WHERE claim_id IN (
            SELECT id FROM claims WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM finding_claims WHERE finding_id IN (
            SELECT id FROM findings WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM finding_sources WHERE finding_id IN (
            SELECT id FROM findings WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM finding_alternatives WHERE finding_id IN (
            SELECT id FROM findings WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    for table in (
        "deliverable_block_claims",
        "deliverable_block_findings",
        "deliverable_block_options",
    ):
        connection.execute(
            f"""
            DELETE FROM {table} WHERE deliverable_block_id IN (
                SELECT id FROM deliverable_blocks WHERE project_id = ?
            )
            """,
            (project_id,),
        )
    connection.execute(
        """
        UPDATE deliverable_block_revisions
        SET review_decision_id = NULL, override_decision_id = NULL
        WHERE deliverable_block_id IN (
            SELECT id FROM deliverable_blocks WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM deliverable_block_revisions WHERE deliverable_block_id IN (
            SELECT id FROM deliverable_blocks WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM review_decisions WHERE deliverable_block_id IN (
            SELECT id FROM deliverable_blocks WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        "DELETE FROM override_decisions WHERE project_id = ?",
        (project_id,),
    )
    connection.execute(
        "DELETE FROM deliverable_blocks WHERE project_id = ?",
        (project_id,),
    )
    connection.execute("DELETE FROM findings WHERE project_id = ?", (project_id,))
    connection.execute("DELETE FROM options WHERE project_id = ?", (project_id,))
    connection.execute("DELETE FROM claims WHERE project_id = ?", (project_id,))
    connection.execute(
        """
        DELETE FROM evidence_excerpts WHERE source_id IN (
            SELECT id FROM sources WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute(
        """
        DELETE FROM source_qa_requirements WHERE source_id IN (
            SELECT id FROM sources WHERE project_id = ?
        )
        """,
        (project_id,),
    )
    connection.execute("DELETE FROM candidate_sources WHERE project_id = ?", (project_id,))
    connection.execute("DELETE FROM sources WHERE project_id = ?", (project_id,))
    connection.execute(
        "DELETE FROM research_questions WHERE project_id = ?",
        (project_id,),
    )
    connection.execute("DELETE FROM briefs WHERE project_id = ?", (project_id,))
    connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))


def _claim_statuses_except(connection: Any, project_id: str) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT id, verification_status FROM claims
        WHERE project_id != ? ORDER BY id
        """,
        (project_id,),
    )
    return {row["id"]: row["verification_status"] for row in rows}


def _block_texts_except(connection: Any, project_id: str) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT id, current_text FROM deliverable_blocks
        WHERE project_id != ? ORDER BY id
        """,
        (project_id,),
    )
    return [(row["id"], row["current_text"]) for row in rows]
