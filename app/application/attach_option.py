from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.ids import allocate_prefixed_id
from app.domain import OptionStatus
from app.projections.report import build_report_projection, build_review_context

ALLOWED_STATUS = {status.value for status in OptionStatus}


class OptionAttachError(ValueError):
    pass


def attach_option_to_block(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    text: Any,
    status: Any = None,
) -> dict[str, Any]:
    """人工写入待验证方向并挂到段落；不改写内部稿，也不改变主张核验状态。"""
    direction = _required_text(text, "方向")
    lifecycle = _optional_text(status) or OptionStatus.CANDIDATE.value
    if lifecycle not in ALLOWED_STATUS:
        raise OptionAttachError("方向状态必须是待验证、需补证、保留、暂缓或排除")
    before_status = _all_claim_statuses(repository)
    before_draft = _block_text(repository, deliverable_block_id)
    now = datetime.now(UTC).isoformat()

    with repository.transaction() as connection:
        block = connection.execute(
            """
            SELECT id, project_id, title, current_text, current_version
            FROM deliverable_blocks WHERE id = ?
            """,
            (deliverable_block_id,),
        ).fetchone()
        if block is None:
            raise OptionAttachError(f"报告段落 {deliverable_block_id} 不存在")
        option_id = allocate_prefixed_id(connection, "options", "O")
        connection.execute(
            """
            INSERT INTO options (
                id, project_id, text, status, schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                option_id,
                block["project_id"],
                direction,
                lifecycle,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO deliverable_block_options VALUES (?, ?)",
            (deliverable_block_id, option_id),
        )

    after_status = _all_claim_statuses(repository)
    after_draft = _block_text(repository, deliverable_block_id)
    if after_draft != before_draft:
        raise OptionAttachError("挂接方向不得改写内部稿")
    if after_status != before_status:
        raise OptionAttachError("挂接方向不得改变主张核验状态")
    review = build_review_context(repository, deliverable_block_id)
    report = build_report_projection(repository, block["project_id"])
    return {
        "option_id": option_id,
        "review_context": review,
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "attach_option",
            "block_title": block["title"],
            "applies_to_version": block["current_version"],
            "treatment": "已挂接待验证方向，未改写内部稿",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已挂接待验证方向。未写成推荐方案，也未改写内部稿或主张核验状态。",
        },
    }


def _required_text(value: Any, label: str) -> str:
    if value is None:
        raise OptionAttachError(f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise OptionAttachError(f"{label}不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _all_claim_statuses(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, verification_status FROM claims ORDER BY rowid"
        )
        return {row["id"]: row["verification_status"] for row in rows}


def _block_text(repository: SqliteRepository, block_id: str) -> str | None:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
        return None if row is None else row["current_text"]
