from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.domain import VerificationStatus
from app.projections.report import build_report_projection, build_review_context

ALLOWED_STATUS = {status.value for status in VerificationStatus}
STATUS_LABELS = {
    "captured": "已捕获，尚未核验",
    "source_checked": "来源已检查",
    "corroborated": "已交叉支持",
    "conflicted": "与其他来源冲突",
    "stale": "可能过时",
    "unverifiable": "当前无法核实",
    "excluded": "不进入本轮交付",
}


class ClaimVerifyError(ValueError):
    pass


def update_claim_verification(
    repository: SqliteRepository,
    deliverable_block_id: str,
    claim_id: str,
    *,
    verification_status: Any,
) -> dict[str, Any]:
    """人工推进主张核验状态；不改写内部稿，也不改变独立核实标记。"""
    next_status = _required_text(verification_status, "核验状态")
    if next_status not in ALLOWED_STATUS:
        raise ClaimVerifyError("核验状态必须是已捕获、来源已检查、已交叉支持、冲突、过时、无法核实或排除")
    claim_key = _required_text(claim_id, "主张")
    before_draft = _block_text(repository, deliverable_block_id)
    before_flags = _all_claim_flags(repository)
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
            raise ClaimVerifyError(f"报告段落 {deliverable_block_id} 不存在")
        claim = connection.execute(
            """
            SELECT id, project_id, verification_status, independently_verified,
                   provenance_scope, delivery_rule
            FROM claims WHERE id = ?
            """,
            (claim_key,),
        ).fetchone()
        if claim is None:
            raise ClaimVerifyError(f"主张 {claim_key} 不存在")
        if claim["project_id"] != block["project_id"]:
            raise ClaimVerifyError("主张不属于当前题目")
        linked = connection.execute(
            """
            SELECT 1 FROM deliverable_block_claims
            WHERE deliverable_block_id = ? AND claim_id = ?
            """,
            (deliverable_block_id, claim_key),
        ).fetchone()
        if linked is None:
            raise ClaimVerifyError("主张未挂到当前段落")
        if claim["verification_status"] == next_status:
            raise ClaimVerifyError("核验状态没有变化")
        connection.execute(
            """
            UPDATE claims
            SET verification_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (next_status, now, claim_key),
        )

    after_draft = _block_text(repository, deliverable_block_id)
    after_flags = _all_claim_flags(repository)
    if after_draft != before_draft:
        raise ClaimVerifyError("更新核验不得改写内部稿")
    if after_flags[claim_key]["independently_verified"] != before_flags[claim_key]["independently_verified"]:
        raise ClaimVerifyError("更新核验不得改变独立核实标记")
    for other_id, before in before_flags.items():
        if other_id == claim_key:
            continue
        if after_flags[other_id] != before:
            raise ClaimVerifyError("更新核验不得改变其他主张")
    review = build_review_context(repository, deliverable_block_id)
    report = build_report_projection(repository, block["project_id"])
    label = STATUS_LABELS[next_status]
    return {
        "claim_id": claim_key,
        "verification_status": next_status,
        "review_context": review,
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "verify_claim",
            "block_title": block["title"],
            "applies_to_version": block["current_version"],
            "treatment": f"主张核验已记为{label}",
            "current_text_unchanged": True,
            "verification_status_unchanged": False,
            "independently_verified_unchanged": True,
            "message": (
                f"已将主张核验记为「{label}」。未改写内部稿，"
                "也未把客户提供标成外部独立核实。"
            ),
        },
    }


def _required_text(value: Any, label: str) -> str:
    if value is None:
        raise ClaimVerifyError(f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise ClaimVerifyError(f"{label}不能为空")
    return text


def _all_claim_flags(repository: SqliteRepository) -> dict[str, dict[str, Any]]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, verification_status, independently_verified
            FROM claims ORDER BY rowid
            """
        )
        return {
            row["id"]: {
                "verification_status": row["verification_status"],
                "independently_verified": row["independently_verified"],
            }
            for row in rows
        }


def _block_text(repository: SqliteRepository, block_id: str) -> str | None:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
        return None if row is None else row["current_text"]
