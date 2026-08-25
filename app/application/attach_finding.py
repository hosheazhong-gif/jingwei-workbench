from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.ids import allocate_prefixed_id
from app.projections.report import build_report_projection, build_review_context

ALLOWED_CONFIDENCE = {
    "low": "弱",
    "medium": "中",
    "high": "高",
    "low_to_medium": "弱到中",
}


class FindingAttachError(ValueError):
    pass


def attach_finding_to_block(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    text: Any,
    claim_ids: Any = None,
    alternative: Any = None,
    confidence: Any = None,
) -> dict[str, Any]:
    """人工写入综合判断并挂到段落；不改写内部稿，也不改变主张核验状态。"""
    observation = _required_text(text, "判断")
    strength = _optional_text(confidence) or "low"
    if strength not in ALLOWED_CONFIDENCE:
        raise FindingAttachError("判断强度必须是弱、中、高或弱到中")
    alt = _optional_text(alternative)
    support_ids = _claim_id_list(claim_ids)
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
            raise FindingAttachError(f"报告段落 {deliverable_block_id} 不存在")
        _require_project_claims(connection, block["project_id"], support_ids)
        finding_id = allocate_prefixed_id(connection, "findings", "F")
        connection.execute(
            """
            INSERT INTO findings (
                id, project_id, text, confidence, schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                finding_id,
                block["project_id"],
                observation,
                strength,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        for claim_id in support_ids:
            connection.execute(
                "INSERT INTO finding_claims VALUES (?, ?, 'supports')",
                (finding_id, claim_id),
            )
        if alt:
            connection.execute(
                "INSERT INTO finding_alternatives VALUES (?, 1, ?)",
                (finding_id, alt),
            )
        connection.execute(
            "INSERT INTO deliverable_block_findings VALUES (?, ?)",
            (deliverable_block_id, finding_id),
        )

    after_status = _all_claim_statuses(repository)
    after_draft = _block_text(repository, deliverable_block_id)
    if after_draft != before_draft:
        raise FindingAttachError("挂接判断不得改写内部稿")
    if after_status != before_status:
        raise FindingAttachError("挂接判断不得改变主张核验状态")
    review = build_review_context(repository, deliverable_block_id)
    report = build_report_projection(repository, block["project_id"])
    return {
        "finding_id": finding_id,
        "review_context": review,
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "attach_finding",
            "block_title": block["title"],
            "applies_to_version": block["current_version"],
            "treatment": "已挂接综合判断，未改写内部稿",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已挂接综合判断。未把主张标成已核实，也未改写内部稿。",
        },
    }


def _claim_id_list(raw: Any) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, list):
        values = raw
    else:
        raise FindingAttachError("支持主张必须是编号列表")
    result: list[str] = []
    for item in values:
        claim_id = _optional_text(item)
        if claim_id:
            result.append(claim_id)
    return result


def _require_project_claims(connection: Any, project_id: str, claim_ids: list[str]) -> None:
    for claim_id in claim_ids:
        row = connection.execute(
            "SELECT id, project_id FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise FindingAttachError(f"主张 {claim_id} 不存在")
        if row["project_id"] != project_id:
            raise FindingAttachError("支持主张不属于当前题目")


def _required_text(value: Any, label: str) -> str:
    if value is None:
        raise FindingAttachError(f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise FindingAttachError(f"{label}不能为空")
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
