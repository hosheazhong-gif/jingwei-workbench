from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository

_CONFIDENCE_LABELS = {
    "low": "弱",
    "medium": "中",
    "high": "高",
    "low_to_medium": "弱到中",
}
_OPTION_STATUS_LABELS = {
    "candidate": "待验证",
    "needs_evidence": "需补证",
    "retained": "保留",
    "deferred": "暂缓",
    "excluded": "排除",
}


def build_report_projection(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    with repository.connect() as connection:
        project = connection.execute(
            """
            SELECT id, name, template_key, execution_strategy_key, stage,
                   decision_gate, schema_version
            FROM projects WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if project is None:
            raise KeyError(f"项目 {project_id} 不存在")
        blocks = connection.execute(
            """
            SELECT id, title, current_text, restriction,
                   delivery_status, current_version
            FROM deliverable_blocks
            WHERE project_id = ?
            ORDER BY rowid
            """,
            (project_id,),
        ).fetchall()
        claim_ids = _ids_by_block(
            connection, "deliverable_block_claims", "claim_id"
        )
        finding_ids = _ids_by_block(
            connection, "deliverable_block_findings", "finding_id"
        )
        option_ids = _ids_by_block(
            connection, "deliverable_block_options", "option_id"
        )

    projected_blocks = []
    for block in blocks:
        item = dict(block)
        block_id = item["id"]
        item["claim_ids"] = claim_ids.get(block_id, [])
        item["finding_ids"] = finding_ids.get(block_id, [])
        item["option_ids"] = option_ids.get(block_id, [])
        projected_blocks.append(item)
    return {
        "project": dict(project),
        "blocks": projected_blocks,
    }


def build_review_context(
    repository: SqliteRepository, deliverable_block_id: str
) -> dict[str, Any]:
    with repository.connect() as connection:
        block = connection.execute(
            """
            SELECT id, project_id, title, current_text, restriction,
                   delivery_status, current_version
            FROM deliverable_blocks WHERE id = ?
            """,
            (deliverable_block_id,),
        ).fetchone()
        if block is None:
            raise KeyError(f"报告段落 {deliverable_block_id} 不存在")
        rows = connection.execute(
            """
            SELECT c.id AS claim_id, c.text AS claim_text, c.epistemic_type,
                   c.verification_status, c.provenance_scope,
                   c.independently_verified, c.delivery_rule,
                   s.id AS source_id, s.title AS source_title, s.kind AS source_kind,
                   s.availability AS source_availability, s.limitation AS source_limitation,
                   s.original_url AS source_url, s.file_name AS source_file,
                   e.id AS evidence_id, e.excerpt, e.locator_json, e.context_limit
            FROM deliverable_block_claims dbc
            JOIN claims c ON c.id = dbc.claim_id
            LEFT JOIN sources s ON s.id = c.source_id
            LEFT JOIN claim_evidence ce ON ce.claim_id = c.id
            LEFT JOIN evidence_excerpts e ON e.id = ce.evidence_excerpt_id
            WHERE dbc.deliverable_block_id = ?
            ORDER BY c.rowid, e.rowid
            """,
            (deliverable_block_id,),
        ).fetchall()
        finding_ids = [
            row["finding_id"]
            for row in connection.execute(
                """
                SELECT finding_id FROM deliverable_block_findings
                WHERE deliverable_block_id = ?
                ORDER BY rowid
                """,
                (deliverable_block_id,),
            )
        ]
        option_ids = [
            row["option_id"]
            for row in connection.execute(
                """
                SELECT option_id FROM deliverable_block_options
                WHERE deliverable_block_id = ?
                ORDER BY rowid
                """,
                (deliverable_block_id,),
            )
        ]
        latest_review = connection.execute(
            """
            SELECT id, action, reason, actor, target_version, created_at
            FROM review_decisions
            WHERE deliverable_block_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (deliverable_block_id,),
        ).fetchone()
        latest_override = connection.execute(
            """
            SELECT id, handling, reason, review_trigger, target_version, created_at
            FROM override_decisions
            WHERE deliverable_block_id = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (deliverable_block_id,),
        ).fetchone()
        project_override = connection.execute(
            """
            SELECT id, handling, reason, review_trigger, target_version, created_at
            FROM override_decisions
            WHERE project_id = ? AND deliverable_block_id IS NULL
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (block["project_id"],),
        ).fetchone()
        pending_revisions = [
            {
                "id": row["id"],
                "version": row["version"],
                "body": row["body"],
                "origin": row["origin"],
            }
            for row in connection.execute(
                """
                SELECT id, version, body, origin
                FROM deliverable_block_revisions
                WHERE deliverable_block_id = ? AND adopted = 0
                ORDER BY version
                """,
                (deliverable_block_id,),
            )
        ]
        prior_row = connection.execute(
            """
            SELECT version, body
            FROM deliverable_block_revisions
            WHERE deliverable_block_id = ?
              AND version < ?
              AND body != ?
            ORDER BY version DESC
            LIMIT 1
            """,
            (
                deliverable_block_id,
                block["current_version"],
                block["current_text"],
            ),
        ).fetchone()
        prior_revision = None
        if prior_row is not None:
            prior_revision = {
                "version": prior_row["version"],
                "body": prior_row["body"],
            }
        findings = _findings_for_block(connection, finding_ids)
        options = _options_for_block(connection, option_ids)
        suggestions = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "kind_label": "总判断" if row["kind"] == "finding" else "可试方向",
                "text": row["text"],
                "status": row["status"],
                "limitation": row["limitation"],
            }
            for row in connection.execute(
                """
                SELECT id, kind, text, status, limitation
                FROM model_suggestions
                WHERE deliverable_block_id = ? AND status = 'pending'
                ORDER BY rowid
                """,
                (deliverable_block_id,),
            )
        ]

    claims: dict[str, dict[str, Any]] = {}
    evidence_by_claim: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        claim_id = row["claim_id"]
        if claim_id not in claims:
            claims[claim_id] = {
                "id": claim_id,
                "text": row["claim_text"],
                "epistemic_type": row["epistemic_type"],
                "verification_status": row["verification_status"],
                "provenance_scope": row["provenance_scope"],
                "independently_verified": _decode_bool(row["independently_verified"]),
                "delivery_rule": row["delivery_rule"],
                "source": {
                    "id": row["source_id"],
                    "title": row["source_title"],
                    "kind": row["source_kind"],
                    "availability": row["source_availability"],
                    "limitation": row["source_limitation"],
                    "original_url": row["source_url"],
                    "file_name": row["source_file"],
                },
            }
        if row["evidence_id"] is not None:
            evidence_by_claim[claim_id].append(
                {
                    "id": row["evidence_id"],
                    "excerpt": row["excerpt"],
                    "locator": json.loads(row["locator_json"]),
                    "context_limit": row["context_limit"],
                }
            )
    for claim_id, claim in claims.items():
        claim["evidence"] = evidence_by_claim[claim_id]

    block_payload = dict(block)
    block_payload["finding_ids"] = finding_ids
    block_payload["option_ids"] = option_ids
    return {
        "block": block_payload,
        "claims": list(claims.values()),
        "findings": findings,
        "options": options,
        "latest_review": dict(latest_review) if latest_review is not None else None,
        "latest_override": dict(latest_override) if latest_override is not None else None,
        "project_override": dict(project_override) if project_override is not None else None,
        "pending_revisions": pending_revisions,
        "prior_revision": prior_revision,
        "suggestions": suggestions,
    }


def _findings_for_block(connection: Any, finding_ids: list[str]) -> list[dict[str, Any]]:
    if not finding_ids:
        return []
    placeholders = ",".join("?" * len(finding_ids))
    rows = connection.execute(
        f"""
        SELECT id, text, confidence
        FROM findings
        WHERE id IN ({placeholders})
        """,
        finding_ids,
    ).fetchall()
    by_id = {
        row["id"]: {
            "id": row["id"],
            "text": row["text"],
            "confidence": row["confidence"],
            "confidence_label": _CONFIDENCE_LABELS.get(
                row["confidence"] or "", row["confidence"] or "未标强度"
            ),
            "supporting_claims": [],
            "alternatives": [],
        }
        for row in rows
    }
    claim_rows = connection.execute(
        f"""
        SELECT fc.finding_id, c.id AS claim_id, c.text AS claim_text
        FROM finding_claims fc
        JOIN claims c ON c.id = fc.claim_id
        WHERE fc.finding_id IN ({placeholders})
        ORDER BY fc.rowid
        """,
        finding_ids,
    ).fetchall()
    for row in claim_rows:
        finding = by_id.get(row["finding_id"])
        if finding is not None:
            finding["supporting_claims"].append(
                {"id": row["claim_id"], "text": row["claim_text"]}
            )
    alt_rows = connection.execute(
        f"""
        SELECT finding_id, text
        FROM finding_alternatives
        WHERE finding_id IN ({placeholders})
        ORDER BY position
        """,
        finding_ids,
    ).fetchall()
    for row in alt_rows:
        finding = by_id.get(row["finding_id"])
        if finding is not None:
            finding["alternatives"].append(row["text"])
    return [by_id[finding_id] for finding_id in finding_ids if finding_id in by_id]


def _options_for_block(connection: Any, option_ids: list[str]) -> list[dict[str, Any]]:
    if not option_ids:
        return []
    placeholders = ",".join("?" * len(option_ids))
    rows = connection.execute(
        f"""
        SELECT id, text, status
        FROM options
        WHERE id IN ({placeholders})
        """,
        option_ids,
    ).fetchall()
    by_id = {
        row["id"]: {
            "id": row["id"],
            "text": row["text"],
            "status": row["status"],
            "status_label": _OPTION_STATUS_LABELS.get(
                row["status"] or "", row["status"] or "未标状态"
            ),
        }
        for row in rows
    }
    return [by_id[option_id] for option_id in option_ids if option_id in by_id]


def _ids_by_block(
    connection: Any, table: str, id_column: str
) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)
    rows = connection.execute(
        f"SELECT deliverable_block_id, {id_column} AS related_id FROM {table} ORDER BY rowid"
    )
    for row in rows:
        grouped[row["deliverable_block_id"]].append(row["related_id"])
    return grouped


def _decode_bool(value: int | None) -> bool | None:
    if value is None:
        return None
    return bool(value)
