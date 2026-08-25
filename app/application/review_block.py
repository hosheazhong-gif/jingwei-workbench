from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.projections.report import build_review_context

REVIEW_ACTIONS = frozenset({"approve", "modify", "exclude"})
OVERRIDE_HANDLINGS = frozenset({"assumption", "exclude", "scenario"})
_ACTION_LABELS = {
    "approve": "批准进入本版",
    "modify": "退回修改",
    "exclude": "从本版排除",
}
_HANDLING_LABELS = {
    "assumption": "按假设推进",
    "exclude": "从本版排除",
    "scenario": "按情景表达",
}
_DEFAULT_REVIEW_REASONS = {
    "approve": "批准当前段落进入本版",
    "modify": "退回修改，内部稿保持现稿",
    "exclude": "从本版排除，内部稿正文保留",
}
_DEFAULT_OVERRIDE_REASONS = {
    "assumption": "资料不足，按假设推进",
    "exclude": "资料不足，从本版排除",
    "scenario": "资料不足，按情景表达",
}
_DEFAULT_TRIGGER = "原附件重新提供或获得租户、空间、经营底表"


class ReviewError(ValueError):
    pass


def record_review_decision(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    action: str,
    reason: str | None = None,
    proposed_text: str | None = None,
    actor: str = "analyst",
) -> dict[str, Any]:
    if action not in REVIEW_ACTIONS:
        raise ReviewError("评审动作必须是批准、退回修改或从本版排除")

    with repository.transaction() as connection:
        block = _require_block(connection, deliverable_block_id)
        statuses_before = _claim_statuses(connection, deliverable_block_id)
        _ensure_current_snapshot(connection, block)
        decision_id = _allocate_id(connection, "review_decisions", "RV")
        now = datetime.now(UTC).isoformat()
        filled_reason = (reason or "").strip() or _DEFAULT_REVIEW_REASONS[action]
        target_version = int(block["current_version"])
        cleaned_proposal = (proposed_text or "").strip()
        if action == "modify" and cleaned_proposal:
            target_version = _next_version(connection, deliverable_block_id)
        connection.execute(
            """
            INSERT INTO review_decisions (
                id, deliverable_block_id, action, reason, actor, created_at,
                target_version, schema_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                deliverable_block_id,
                action,
                filled_reason,
                actor,
                now,
                target_version,
                SCHEMA_VERSION,
                now,
            ),
        )
        candidate = None
        if action == "modify" and cleaned_proposal:
            candidate = _insert_revision(
                connection,
                block_id=deliverable_block_id,
                version=target_version,
                body=cleaned_proposal,
                origin="review_candidate",
                review_decision_id=decision_id,
                override_decision_id=None,
                created_at=now,
            )
        current_text = connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (deliverable_block_id,),
        ).fetchone()["current_text"]
        statuses_after = _claim_statuses(connection, deliverable_block_id)
        _assert_protected(block["current_text"], current_text, statuses_before, statuses_after)
        decision = {
            "id": decision_id,
            "deliverable_block_id": deliverable_block_id,
            "action": action,
            "reason": filled_reason,
            "actor": actor,
            "target_version": target_version,
        }

    return _payload(
        repository,
        record_kind="review",
        block=block,
        treatment=_ACTION_LABELS[action],
        applies_to_version=target_version,
        current_text_unchanged=True,
        decision_key="review_decision",
        decision=decision,
        pending_revision=candidate,
    )


def record_override_decision(
    repository: SqliteRepository,
    *,
    project_id: str | None = None,
    deliverable_block_id: str | None = None,
    handling: str,
    reason: str | None = None,
    review_trigger: str | None = None,
    proposed_text: str | None = None,
) -> dict[str, Any]:
    if handling not in OVERRIDE_HANDLINGS:
        raise ReviewError("风险处理必须是按假设、从本版排除或按情景表达")
    if not project_id and not deliverable_block_id:
        raise ReviewError("覆盖决定必须指定项目或段落")

    with repository.transaction() as connection:
        block = None
        if deliverable_block_id:
            block = _require_block(connection, deliverable_block_id)
            project_id = str(block["project_id"])
            statuses_before = _claim_statuses(connection, deliverable_block_id)
            _ensure_current_snapshot(connection, block)
            target_version = int(block["current_version"])
        else:
            if not _project_exists(connection, project_id):
                raise ReviewError(f"项目 {project_id} 不存在")
            statuses_before = _all_claim_statuses(connection, project_id)
            target_version = 1
        decision_id = _allocate_id(connection, "override_decisions", "OVR")
        now = datetime.now(UTC).isoformat()
        filled_reason = (reason or "").strip() or _DEFAULT_OVERRIDE_REASONS[handling]
        filled_trigger = (review_trigger or "").strip() or _DEFAULT_TRIGGER
        cleaned_proposal = (proposed_text or "").strip()
        if block is not None and cleaned_proposal:
            target_version = _next_version(connection, deliverable_block_id)
        connection.execute(
            """
            INSERT INTO override_decisions (
                id, project_id, deliverable_block_id, handling, reason,
                review_trigger, target_version, created_at, schema_version, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision_id,
                project_id,
                deliverable_block_id,
                handling,
                filled_reason,
                filled_trigger,
                target_version,
                now,
                SCHEMA_VERSION,
                now,
            ),
        )
        candidate = None
        if block is not None and cleaned_proposal:
            candidate = _insert_revision(
                connection,
                block_id=deliverable_block_id,
                version=target_version,
                body=cleaned_proposal,
                origin="override_candidate",
                review_decision_id=None,
                override_decision_id=decision_id,
                created_at=now,
            )
        if block is not None:
            current_text = connection.execute(
                "SELECT current_text FROM deliverable_blocks WHERE id = ?",
                (deliverable_block_id,),
            ).fetchone()["current_text"]
            statuses_after = _claim_statuses(connection, deliverable_block_id)
            _assert_protected(
                block["current_text"], current_text, statuses_before, statuses_after
            )
        else:
            statuses_after = _all_claim_statuses(connection, project_id)
            if statuses_before != statuses_after:
                raise ReviewError("覆盖决定不得改变主张核验状态")
        decision = {
            "id": decision_id,
            "project_id": project_id,
            "deliverable_block_id": deliverable_block_id,
            "handling": handling,
            "reason": filled_reason,
            "review_trigger": filled_trigger,
            "target_version": target_version,
        }

    return _payload(
        repository,
        record_kind="override",
        block=block,
        treatment=_HANDLING_LABELS[handling],
        applies_to_version=target_version,
        current_text_unchanged=True,
        decision_key="override_decision",
        decision=decision,
        pending_revision=candidate,
        project_id=project_id,
    )


def propose_block_revision(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    body: Any,
) -> dict[str, Any]:
    """人工保存本段改稿为候选版本；不立刻替换内部稿，也不改变主张核验状态。"""
    cleaned = "" if body is None else str(body).strip()
    if not cleaned:
        raise ReviewError("改稿正文不能为空")

    with repository.transaction() as connection:
        block = _require_block(connection, deliverable_block_id)
        statuses_before = _claim_statuses(connection, deliverable_block_id)
        if cleaned == (block["current_text"] or "").strip():
            raise ReviewError("改稿与当前内部稿相同，没有新的候选版本")
        _ensure_current_snapshot(connection, block)
        now = datetime.now(UTC).isoformat()
        target_version = _next_version(connection, deliverable_block_id)
        candidate = _insert_revision(
            connection,
            block_id=deliverable_block_id,
            version=target_version,
            body=cleaned,
            origin="review_candidate",
            review_decision_id=None,
            override_decision_id=None,
            created_at=now,
        )
        current_text = connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (deliverable_block_id,),
        ).fetchone()["current_text"]
        statuses_after = _claim_statuses(connection, deliverable_block_id)
        _assert_protected(block["current_text"], current_text, statuses_before, statuses_after)

    return _payload(
        repository,
        record_kind="propose",
        block=block,
        treatment="已保存改稿候选，未替换当前内部稿",
        applies_to_version=target_version,
        current_text_unchanged=True,
        decision_key="proposed_revision",
        decision=candidate,
        pending_revision=candidate,
    )


def adopt_revision(
    repository: SqliteRepository,
    deliverable_block_id: str,
    version: int,
) -> dict[str, Any]:
    with repository.transaction() as connection:
        block = _require_block(connection, deliverable_block_id)
        statuses_before = _claim_statuses(connection, deliverable_block_id)
        revision = connection.execute(
            """
            SELECT id, version, body, origin, adopted
            FROM deliverable_block_revisions
            WHERE deliverable_block_id = ? AND version = ?
            """,
            (deliverable_block_id, version),
        ).fetchone()
        if revision is None:
            raise ReviewError(f"段落没有版本 {version}")
        if revision["origin"] == "snapshot":
            raise ReviewError("当前快照无需再次采用")
        if revision["adopted"]:
            raise ReviewError("该候选版本已经是当前稿")
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """
            UPDATE deliverable_blocks
            SET current_text = ?, current_version = ?, updated_at = ?
            WHERE id = ?
            """,
            (revision["body"], version, now, deliverable_block_id),
        )
        connection.execute(
            """
            UPDATE deliverable_block_revisions
            SET adopted = 1
            WHERE id = ?
            """,
            (revision["id"],),
        )
        statuses_after = _claim_statuses(connection, deliverable_block_id)
        if statuses_before != statuses_after:
            raise ReviewError("采用候选版本不得改变主张核验状态")
        adopted = {
            "id": revision["id"],
            "version": version,
            "origin": revision["origin"],
        }

    return _payload(
        repository,
        record_kind="adopt",
        block=block,
        treatment="采用候选版本替换当前内部稿",
        applies_to_version=version,
        current_text_unchanged=False,
        decision_key="adopted_revision",
        decision=adopted,
        pending_revision=None,
    )


def _payload(
    repository: SqliteRepository,
    *,
    record_kind: str,
    block: dict[str, Any] | None,
    treatment: str,
    applies_to_version: int,
    current_text_unchanged: bool,
    decision_key: str,
    decision: dict[str, Any],
    pending_revision: dict[str, Any] | None,
    project_id: str | None = None,
) -> dict[str, Any]:
    title = block["title"] if block else None
    if record_kind == "adopt":
        message = (
            f"已将候选版本 {applies_to_version} 替换为「{title}」的当前内部稿。"
            "证据核验状态未改变。"
        )
    elif record_kind == "propose":
        message = (
            f"已保存「{title}」的改稿候选版本 {applies_to_version}。"
            "内部稿仍是现稿，确认采用后才替换。证据核验状态未改变。"
        )
    elif title:
        unchanged = "内部稿正文未改写。" if current_text_unchanged else ""
        message = (
            f"记录已创建。作用于「{title}」版本 {applies_to_version}。"
            f"本版{treatment}。证据核验状态未改变。{unchanged}"
        )
    else:
        message = (
            f"记录已创建。作用于项目版本 {applies_to_version}。"
            f"本版{treatment}。证据核验状态未改变。内部稿正文未改写。"
        )
    result: dict[str, Any] = {
        "confirmation": {
            "recorded": True,
            "record_kind": record_kind,
            "block_title": title,
            "applies_to_version": applies_to_version,
            "treatment": treatment,
            "current_text_unchanged": current_text_unchanged,
            "verification_status_unchanged": True,
            "message": message,
        },
        decision_key: decision,
        "pending_revision": pending_revision,
    }
    if block is not None:
        result["review_context"] = build_review_context(repository, block["id"])
    elif project_id:
        result["project_id"] = project_id
    return result


def _require_block(connection: Any, block_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT id, project_id, title, current_text, current_version
        FROM deliverable_blocks WHERE id = ?
        """,
        (block_id,),
    ).fetchone()
    if row is None:
        raise ReviewError(f"报告段落 {block_id} 不存在")
    return dict(row)


def _project_exists(connection: Any, project_id: str | None) -> bool:
    if not project_id:
        return False
    row = connection.execute(
        "SELECT 1 FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    return row is not None


def _claim_statuses(connection: Any, block_id: str) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT c.id, c.verification_status
        FROM deliverable_block_claims dbc
        JOIN claims c ON c.id = dbc.claim_id
        WHERE dbc.deliverable_block_id = ?
        ORDER BY c.rowid
        """,
        (block_id,),
    )
    return {row["id"]: row["verification_status"] for row in rows}


def _all_claim_statuses(connection: Any, project_id: str) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT id, verification_status FROM claims
        WHERE project_id = ? ORDER BY rowid
        """,
        (project_id,),
    )
    return {row["id"]: row["verification_status"] for row in rows}


def _assert_protected(
    text_before: str,
    text_after: str,
    statuses_before: dict[str, str],
    statuses_after: dict[str, str],
) -> None:
    if text_before != text_after:
        raise ReviewError("评审或覆盖不得自动改写内部稿")
    if statuses_before != statuses_after:
        raise ReviewError("评审或覆盖不得改变主张核验状态")


def _ensure_current_snapshot(connection: Any, block: dict[str, Any]) -> None:
    existing = connection.execute(
        """
        SELECT 1 FROM deliverable_block_revisions
        WHERE deliverable_block_id = ? AND version = ?
        """,
        (block["id"], block["current_version"]),
    ).fetchone()
    if existing is not None:
        return
    now = datetime.now(UTC).isoformat()
    _insert_revision(
        connection,
        block_id=block["id"],
        version=int(block["current_version"]),
        body=block["current_text"],
        origin="snapshot",
        review_decision_id=None,
        override_decision_id=None,
        created_at=now,
        adopted=1,
    )


def _next_version(connection: Any, block_id: str) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(version), 0) AS max_version
        FROM deliverable_block_revisions
        WHERE deliverable_block_id = ?
        """,
        (block_id,),
    ).fetchone()
    return int(row["max_version"]) + 1


def _insert_revision(
    connection: Any,
    *,
    block_id: str,
    version: int,
    body: str,
    origin: str,
    review_decision_id: str | None,
    override_decision_id: str | None,
    created_at: str,
    adopted: int = 0,
) -> dict[str, Any]:
    revision_id = f"{block_id}-v{version}"
    round_index = _current_round(connection, block_id)
    connection.execute(
        """
        INSERT INTO deliverable_block_revisions (
            id, deliverable_block_id, version, body, origin, adopted,
            review_decision_id, override_decision_id, created_at, schema_version,
            round_index
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            revision_id,
            block_id,
            version,
            body,
            origin,
            adopted,
            review_decision_id,
            override_decision_id,
            created_at,
            SCHEMA_VERSION,
            round_index,
        ),
    )
    return {
        "id": revision_id,
        "deliverable_block_id": block_id,
        "version": version,
        "body": body,
        "origin": origin,
        "round_index": round_index,
        "adopted": bool(adopted),
        "review_decision_id": review_decision_id,
        "override_decision_id": override_decision_id,
    }


def _current_round(connection: Any, block_id: str) -> int:
    """这一版是第几轮收下的。

    一篇稿只保留一套段落对象，修订按轮次标记，不做每轮一套稿。段落对象仍
    只有一套，轮次记在版本上，所以补料重写不会把上一轮交出去的那一版弄丢。
    """
    row = connection.execute(
        """
        SELECT p.current_round AS current_round
        FROM deliverable_blocks b
        JOIN projects p ON p.id = b.project_id
        WHERE b.id = ?
        """,
        (block_id,),
    ).fetchone()
    if row is None:
        return 1
    return int(row["current_round"] or 1)


def _allocate_id(connection: Any, table: str, prefix: str) -> str:
    used = {
        row["id"]
        for row in connection.execute(f"SELECT id FROM {table}")
    }
    index = 1
    while f"{prefix}-{index:03d}" in used:
        index += 1
    return f"{prefix}-{index:03d}"
