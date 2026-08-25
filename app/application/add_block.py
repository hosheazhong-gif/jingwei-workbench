from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.ids import allocate_prefixed_id
from app.projections.report import build_report_projection, build_review_context

DEFAULT_RESTRICTION = "尚无来源与主张；不能当作已核实结论。"


class BlockWriteError(ValueError):
    pass


def add_deliverable_block(
    repository: SqliteRepository,
    project_id: str,
    *,
    title: Any,
    current_text: Any,
    restriction: Any = None,
) -> dict[str, Any]:
    """人工新增内部稿段落；不生成结论，不改写已有段落，也不改变主张核验状态。"""
    if not repository.has_project(project_id):
        raise BlockWriteError(f"项目 {project_id} 不存在")
    block_title = _required_text(title, "段落标题")
    body = _required_text(current_text, "段落正文")
    limit = _optional_text(restriction) or DEFAULT_RESTRICTION
    before_status = _claim_statuses(repository, project_id)
    before_draft = _block_texts(repository, project_id)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        block_id = allocate_prefixed_id(connection, "deliverable_blocks", "DB")
        connection.execute(
            """
            INSERT INTO deliverable_blocks (
                id, project_id, title, current_text, restriction,
                delivery_status, current_version, schema_version,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?)
            """,
            (
                block_id,
                project_id,
                block_title,
                body,
                limit,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO deliverable_block_revisions (
                id, deliverable_block_id, version, body, origin, adopted,
                review_decision_id, override_decision_id, created_at, schema_version
            ) VALUES (?, ?, 1, ?, 'snapshot', 1, NULL, NULL, ?, ?)
            """,
            (f"{block_id}-v1", block_id, body, now, SCHEMA_VERSION),
        )

    after_status = _claim_statuses(repository, project_id)
    after_draft = _block_texts(repository, project_id)
    if after_status != before_status:
        raise BlockWriteError("新增段落不得改变主张核验状态")
    if after_draft[: len(before_draft)] != before_draft:
        raise BlockWriteError("新增段落不得改写已有内部稿")
    report = build_report_projection(repository, project_id)
    review = build_review_context(repository, block_id)
    return {
        "block_id": block_id,
        "report": report,
        "review_context": review,
        "confirmation": {
            "recorded": True,
            "record_kind": "add_block",
            "block_title": block_title,
            "applies_to_version": 1,
            "treatment": "新增段落，未生成主张",
            "verification_status_unchanged": True,
            "existing_current_text_unchanged": True,
            "current_text_unchanged": True,
            "message": "已新增人工段落。未生成主张，也未改写已有内部稿。",
        },
    }


def remove_deliverable_block(
    repository: SqliteRepository, deliverable_block_id: str
) -> dict[str, Any]:
    """去掉尚未挂主张的一节。不改其他段落，也不改变主张核验状态。"""
    with repository.connect() as connection:
        block = connection.execute(
            "SELECT id, project_id, title, current_text FROM deliverable_blocks WHERE id = ?",
            (deliverable_block_id,),
        ).fetchone()
        if block is None:
            raise BlockWriteError(f"段落 {deliverable_block_id} 不存在")
        project_id = block["project_id"]
        siblings = connection.execute(
            "SELECT COUNT(*) AS n FROM deliverable_blocks WHERE project_id = ?",
            (project_id,),
        ).fetchone()["n"]
        if siblings <= 1:
            raise BlockWriteError("这篇稿至少要留一节")
        linked = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM deliverable_block_claims WHERE deliverable_block_id = ?)
              + (SELECT COUNT(*) FROM deliverable_block_findings WHERE deliverable_block_id = ?)
              + (SELECT COUNT(*) FROM deliverable_block_options WHERE deliverable_block_id = ?)
                AS n
            """,
            (deliverable_block_id, deliverable_block_id, deliverable_block_id),
        ).fetchone()["n"]
        if linked:
            raise BlockWriteError("这一节已挂主张或判断，不能去掉")
    before_status = _claim_statuses(repository, project_id)
    before_others = _other_block_texts(repository, project_id, deliverable_block_id)
    with repository.transaction() as connection:
        connection.execute(
            "DELETE FROM model_suggestions WHERE deliverable_block_id = ?",
            (deliverable_block_id,),
        )
        connection.execute(
            """
            UPDATE deliverable_block_revisions
            SET review_decision_id = NULL, override_decision_id = NULL
            WHERE deliverable_block_id = ?
            """,
            (deliverable_block_id,),
        )
        connection.execute(
            "DELETE FROM deliverable_block_revisions WHERE deliverable_block_id = ?",
            (deliverable_block_id,),
        )
        connection.execute(
            "DELETE FROM review_decisions WHERE deliverable_block_id = ?",
            (deliverable_block_id,),
        )
        connection.execute(
            "DELETE FROM override_decisions WHERE deliverable_block_id = ?",
            (deliverable_block_id,),
        )
        connection.execute(
            "DELETE FROM deliverable_blocks WHERE id = ?",
            (deliverable_block_id,),
        )
    if _claim_statuses(repository, project_id) != before_status:
        raise BlockWriteError("去掉一节不得改变主张核验状态")
    if _other_block_texts(repository, project_id, deliverable_block_id) != before_others:
        raise BlockWriteError("去掉一节不得改写其他内部稿")
    return {
        "deleted": True,
        "block_id": deliverable_block_id,
        "report": build_report_projection(repository, project_id),
        "confirmation": {
            "recorded": True,
            "record_kind": "remove_block",
            "block_title": block["title"],
            "verification_status_unchanged": True,
            "existing_current_text_unchanged": True,
            "message": "已去掉这一节。其他段落和核验未改。",
        },
    }


def rename_deliverable_block(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    title: Any,
) -> dict[str, Any]:
    """只改这一节的名称；不改正文、版本或主张核验。"""
    from app.projections.workbench import build_workbench_projection

    block_key = _required_text(deliverable_block_id, "段落")
    block_title = _required_text(title, "节名")
    before_status = None
    before_draft = None
    project_id = None
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        block = connection.execute(
            """
            SELECT id, project_id, title, current_text, current_version
            FROM deliverable_blocks WHERE id = ?
            """,
            (block_key,),
        ).fetchone()
        if block is None:
            raise BlockWriteError(f"段落 {block_key} 不存在")
        project_id = block["project_id"]
        prior_version = block["current_version"]
        before_status = _claim_statuses(repository, project_id)
        before_draft = _block_texts(repository, project_id)
        connection.execute(
            """
            UPDATE deliverable_blocks
            SET title = ?, updated_at = ?
            WHERE id = ?
            """,
            (block_title, now, block_key),
        )
    after_status = _claim_statuses(repository, project_id)
    after_draft = _block_texts(repository, project_id)
    if after_draft != before_draft:
        raise BlockWriteError("改节名不得改写内部稿正文")
    if after_status != before_status:
        raise BlockWriteError("改节名不得改变主张核验状态")
    with repository.connect() as connection:
        version = connection.execute(
            "SELECT current_version FROM deliverable_blocks WHERE id = ?",
            (block_key,),
        ).fetchone()["current_version"]
    if version != prior_version:
        raise BlockWriteError("改节名不得推进段落版本")
    return {
        "block_id": block_key,
        "report": build_report_projection(repository, project_id),
        "workbench": build_workbench_projection(repository, project_id),
        "confirmation": {
            "recorded": True,
            "record_kind": "rename_block",
            "block_title": block_title,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "已记下这一节的名称。正文和核验未改。",
        },
    }


def _required_text(value: Any, label: str) -> str:
    if value is None:
        raise BlockWriteError(f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BlockWriteError(f"{label}不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _claim_statuses(repository: SqliteRepository, project_id: str) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, verification_status FROM claims
            WHERE project_id = ? ORDER BY rowid
            """,
            (project_id,),
        )
        return {row["id"]: row["verification_status"] for row in rows}


def _block_texts(repository: SqliteRepository, project_id: str) -> list[str]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT current_text FROM deliverable_blocks
            WHERE project_id = ? ORDER BY rowid
            """,
            (project_id,),
        )
        return [row["current_text"] for row in rows]


def _other_block_texts(
    repository: SqliteRepository, project_id: str, block_id: str
) -> list[tuple[str, str]]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, current_text FROM deliverable_blocks
            WHERE project_id = ? AND id != ? ORDER BY rowid
            """,
            (project_id, block_id),
        ).fetchall()
        return [(row["id"], row["current_text"]) for row in rows]
