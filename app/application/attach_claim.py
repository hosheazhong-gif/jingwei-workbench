from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.create_project import PLACEHOLDER_RESTRICTION
from app.application.ids import allocate_prefixed_id
from app.domain.models import EpistemicType, ProvenanceScope
from app.projections.report import build_report_projection, build_review_context
from app.projections.workbench import build_workbench_projection

CLIENT_DELIVERY_RULE = "据客户提供、口径待补；客户来源不等于外部独立核实。"
MACRO_DELIVERY_RULE = "宏观市场材料只支持宏观判断，不单独证明项目需求。"
CAPTURED_DELIVERY_RULE = "已捕获，尚未核验；不能当作已核实结论。"
ATTACHED_RESTRICTION = "已挂接主张；核验状态仍为已捕获，不能当作已核实结论。"
_EPSTEMIC = {item.value for item in EpistemicType}
_PROVENANCE = {item.value for item in ProvenanceScope}
FEEDBACK_DELIVERY_RULE = "据经理反馈；内部指示，不是客户口径，也不是外部证据。"


class ClaimAttachError(ValueError):
    pass


def attach_claim_to_block(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    source_id: Any,
    excerpt: Any,
    text: Any = None,
    epistemic_type: Any = None,
    provenance_scope: Any = None,
    macro_market: bool = False,
    locator: Any = None,
    context_limit: Any = None,
    locator_kind: Any = None,
) -> dict[str, Any]:
    """人工写入摘录与主张并挂到段落；不解析文件，不改核验状态，也不改写内部稿。"""
    source_key = _required_text(source_id, "来源")
    excerpt_text = _required_text(excerpt, "摘录")
    claim_text = _optional_text(text) or excerpt_text
    kind = _optional_text(epistemic_type) or EpistemicType.FACTUAL_CLAIM.value
    if kind not in _EPSTEMIC:
        raise ClaimAttachError("认识类型必须是来源事实陈述、推断、假设或顾问判断")
    scope = _optional_text(provenance_scope)
    if scope and scope not in _PROVENANCE:
        raise ClaimAttachError("归属只能是客户提供或经理反馈")
    client_provided = scope == ProvenanceScope.CLIENT_PROVIDED.value
    manager_feedback = scope == ProvenanceScope.MANAGER_FEEDBACK.value
    locator_note = _optional_text(locator)
    kind_label = _optional_text(locator_kind) or "manual"
    if kind_label not in {"manual", "snapshot"}:
        kind_label = "manual"
    limit = _optional_text(context_limit)
    before_status = _all_claim_statuses(repository)
    before_draft = _block_text(repository, deliverable_block_id)
    now = datetime.now(UTC).isoformat()

    with repository.transaction() as connection:
        block = connection.execute(
            """
            SELECT id, project_id, title, current_text, restriction, current_version
            FROM deliverable_blocks WHERE id = ?
            """,
            (deliverable_block_id,),
        ).fetchone()
        if block is None:
            raise ClaimAttachError(f"报告段落 {deliverable_block_id} 不存在")
        source = connection.execute(
            """
            SELECT id, project_id, analysis_role FROM sources WHERE id = ?
            """,
            (source_key,),
        ).fetchone()
        if source is None:
            raise ClaimAttachError(f"来源 {source_key} 不存在")
        if source["project_id"] != block["project_id"]:
            raise ClaimAttachError("来源不属于当前题目")
        is_macro = bool(macro_market) or source["analysis_role"] == "macro_market_outlook"
        delivery_rule = _delivery_rule(client_provided, manager_feedback, is_macro)
        excerpt_id = allocate_prefixed_id(connection, "evidence_excerpts", "E")
        claim_id = allocate_prefixed_id(connection, "claims", "C")
        locator_note = locator_note or (
            "快照摘录" if kind_label == "snapshot" else "人工摘录"
        )
        locator_json = json.dumps(
            {
                "kind": kind_label,
                "note": locator_note,
                "region_label": locator_note,
            },
            ensure_ascii=False,
        )
        connection.execute(
            """
            INSERT INTO evidence_excerpts (
                id, source_id, locator_json, excerpt, context_limit,
                schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                excerpt_id,
                source_key,
                locator_json,
                excerpt_text,
                limit,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO claims (
                id, project_id, source_id, text, epistemic_type,
                verification_status, provenance_scope, independently_verified,
                delivery_rule, schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'captured', ?, 0, ?, ?, ?, ?)
            """,
            (
                claim_id,
                block["project_id"],
                source_key,
                claim_text,
                kind,
                scope or None,
                delivery_rule,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO claim_evidence VALUES (?, ?, 'supports')",
            (claim_id, excerpt_id),
        )
        connection.execute(
            "INSERT INTO deliverable_block_claims VALUES (?, ?)",
            (deliverable_block_id, claim_id),
        )
        if block["restriction"] == PLACEHOLDER_RESTRICTION:
            connection.execute(
                """
                UPDATE deliverable_blocks
                SET restriction = ?, updated_at = ?
                WHERE id = ?
                """,
                (ATTACHED_RESTRICTION, now, deliverable_block_id),
            )

    after_status = _all_claim_statuses(repository)
    after_draft = _block_text(repository, deliverable_block_id)
    if after_draft != before_draft:
        raise ClaimAttachError("挂接主张不得改写内部稿")
    if any(after_status.get(claim_id) != status for claim_id, status in before_status.items()):
        raise ClaimAttachError("挂接主张不得改变已有主张核验状态")
    if after_status.get(claim_id) != "captured":
        raise ClaimAttachError("新主张只能是已捕获，尚未核验")
    review = build_review_context(repository, deliverable_block_id)
    report = build_report_projection(repository, block["project_id"])
    return {
        "claim_id": claim_id,
        "excerpt_id": excerpt_id,
        "review_context": review,
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "attach_claim",
            "block_title": block["title"],
            "applies_to_version": block["current_version"],
            "treatment": "已挂接主张，未改写内部稿",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已记下原话。未改给经理的稿，也未标成已核实。",
        },
    }


def unlink_claim_from_block(
    repository: SqliteRepository,
    deliverable_block_id: str,
    claim_id: str,
) -> dict[str, Any]:
    """从这一节拿掉已挂原话；不删除主张、摘录或材料，也不改核验或正文。"""
    block_key = _required_text(deliverable_block_id, "段落")
    claim_key = _required_text(claim_id, "原话")
    before_status = _all_claim_statuses(repository)
    before_draft = _block_text(repository, block_key)
    with repository.transaction() as connection:
        block = connection.execute(
            """
            SELECT id, project_id, title, current_text FROM deliverable_blocks
            WHERE id = ?
            """,
            (block_key,),
        ).fetchone()
        if block is None:
            raise ClaimAttachError(f"报告段落 {block_key} 不存在")
        claim = connection.execute(
            "SELECT id, project_id FROM claims WHERE id = ?",
            (claim_key,),
        ).fetchone()
        if claim is None:
            raise ClaimAttachError(f"主张 {claim_key} 不存在")
        if claim["project_id"] != block["project_id"]:
            raise ClaimAttachError("主张不属于当前题目")
        linked = connection.execute(
            """
            SELECT 1 FROM deliverable_block_claims
            WHERE deliverable_block_id = ? AND claim_id = ?
            """,
            (block_key, claim_key),
        ).fetchone()
        if linked is None:
            raise ClaimAttachError("这一节没有挂这条原话")
        connection.execute(
            """
            DELETE FROM deliverable_block_claims
            WHERE deliverable_block_id = ? AND claim_id = ?
            """,
            (block_key, claim_key),
        )
    after_status = _all_claim_statuses(repository)
    after_draft = _block_text(repository, block_key)
    if after_draft != before_draft:
        raise ClaimAttachError("从这一节拿掉原话不得改写内部稿")
    if after_status != before_status:
        raise ClaimAttachError("从这一节拿掉原话不得改变主张核验状态")
    if claim_key not in after_status:
        raise ClaimAttachError("从这一节拿掉原话不得删除主张")
    workbench = build_workbench_projection(repository, block["project_id"])
    return {
        "claim_id": claim_key,
        "workbench": workbench,
        "confirmation": {
            "recorded": True,
            "record_kind": "unlink_claim",
            "block_title": block["title"],
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "这条原话这节先不用。材料还在，稿未改，核验未改。",
        },
    }


def _delivery_rule(
    client_provided: bool, manager_feedback: bool, is_macro: bool
) -> str:
    parts: list[str] = []
    if client_provided:
        parts.append(CLIENT_DELIVERY_RULE)
    if manager_feedback:
        parts.append(FEEDBACK_DELIVERY_RULE)
    if is_macro:
        parts.append(MACRO_DELIVERY_RULE)
    return " ".join(parts) or CAPTURED_DELIVERY_RULE


def _required_text(value: Any, label: str) -> str:
    if value is None:
        raise ClaimAttachError(f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise ClaimAttachError(f"{label}不能为空")
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
