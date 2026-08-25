from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository


class SampleImportError(ValueError):
    pass


def load_sample(path: Path | str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA_VERSION:
        raise SampleImportError(
            f"只接受 schema {SCHEMA_VERSION}，收到 {data.get('schema_version')!r}"
        )
    return data


def import_sample(repository: SqliteRepository, sample_path: Path | str) -> str:
    data = load_sample(sample_path)
    project = data["project"]
    project_id = project["id"]
    if repository.has_project(project_id):
        raise SampleImportError(f"项目 {project_id} 已存在；导入不会静默覆盖")

    now = datetime.now(UTC).isoformat()
    created_at = data.get("created_at", now)
    with repository.transaction() as connection:
        connection.execute(
            """
            INSERT INTO projects (
                id, name, template_key, execution_strategy_key, stage, decision_gate,
                schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                project["name"],
                project["template_key"],
                project["execution_strategy_key"],
                project.get("stage"),
                project.get("decision_gate"),
                SCHEMA_VERSION,
                created_at,
                now,
            ),
        )

        brief = data["brief"]
        connection.execute(
            """
            INSERT INTO briefs (
                id, project_id, original_context, decision_question, deliverable,
                not_a_final_client_recommendation, schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief["id"],
                project_id,
                brief["original_context"],
                brief["decision_question"],
                brief["deliverable"],
                int(brief.get("not_a_final_client_recommendation", False)),
                SCHEMA_VERSION,
                created_at,
                now,
            ),
        )

        for question in data.get("research_questions", []):
            connection.execute(
                """
                INSERT INTO research_questions (
                    id, project_id, question, enough_for_now, status,
                    schema_version, created_at, updated_at, round_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    question["id"],
                    project_id,
                    question["question"],
                    question.get("enough_for_now"),
                    question.get("status", "not_started"),
                    SCHEMA_VERSION,
                    created_at,
                    now,
                ),
            )

        for source in data.get("sources", []):
            connection.execute(
                """
                INSERT INTO sources (
                    id, project_id, kind, title, file_name, availability,
                    snapshot_path, content_hash, supersedes_source_id, limitation,
                    analysis_role, delivery_use, schema_version, created_at, updated_at,
                    institution, published_at, original_url, original_path,
                    permission, sensitivity, source_quality
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source["id"],
                    project_id,
                    source["kind"],
                    source["title"],
                    source.get("file_name"),
                    source["availability"],
                    source.get("snapshot_path"),
                    source.get("sha256"),
                    source.get("supersedes_source_id"),
                    source.get("limitation"),
                    source.get("analysis_role"),
                    source.get("delivery_use"),
                    SCHEMA_VERSION,
                    created_at,
                    now,
                    source.get("institution"),
                    source.get("published_at"),
                    source.get("original_url"),
                    source.get("original_path"),
                    source.get("permission"),
                    source.get("sensitivity"),
                    source.get("source_quality"),
                ),
            )
            for requirement in source.get("qa_required", []):
                connection.execute(
                    "INSERT INTO source_qa_requirements VALUES (?, ?)",
                    (source["id"], requirement),
                )

        for evidence in data.get("evidence_excerpts", []):
            connection.execute(
                """
                INSERT INTO evidence_excerpts (
                    id, source_id, locator_json, excerpt, context_limit,
                    schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence["id"],
                    evidence["source_id"],
                    json.dumps(evidence["locator"], ensure_ascii=False),
                    evidence["excerpt"],
                    evidence.get("context_limit"),
                    SCHEMA_VERSION,
                    created_at,
                    now,
                ),
            )

        for claim in data.get("claims", []):
            connection.execute(
                """
                INSERT INTO claims (
                    id, project_id, source_id, text, epistemic_type,
                    verification_status, provenance_scope, independently_verified,
                    delivery_rule, schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim["id"],
                    project_id,
                    claim.get("source_id"),
                    claim["text"],
                    claim["epistemic_type"],
                    claim["verification_status"],
                    claim.get("provenance_scope"),
                    _optional_bool(claim.get("independently_verified")),
                    claim.get("delivery_rule"),
                    SCHEMA_VERSION,
                    created_at,
                    now,
                ),
            )
            for evidence_id in claim.get("evidence_excerpt_ids", []):
                connection.execute(
                    "INSERT INTO claim_evidence VALUES (?, ?, 'supports')",
                    (claim["id"], evidence_id),
                )

        for finding in data.get("findings", []):
            connection.execute(
                """
                INSERT INTO findings (
                    id, project_id, text, confidence, schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding["id"],
                    project_id,
                    finding["text"],
                    finding.get("confidence"),
                    SCHEMA_VERSION,
                    created_at,
                    now,
                ),
            )
            for claim_id in finding.get("supporting_claim_ids", []):
                connection.execute(
                    "INSERT INTO finding_claims VALUES (?, ?, 'supports')",
                    (finding["id"], claim_id),
                )
            for source_id in finding.get("supporting_source_ids", []):
                connection.execute(
                    "INSERT INTO finding_sources VALUES (?, ?)",
                    (finding["id"], source_id),
                )
            for position, alternative in enumerate(
                finding.get("alternative_explanations", []), start=1
            ):
                connection.execute(
                    "INSERT INTO finding_alternatives VALUES (?, ?, ?)",
                    (finding["id"], position, alternative),
                )

        for option in data.get("options", []):
            connection.execute(
                """
                INSERT INTO options (
                    id, project_id, text, status, schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    option["id"],
                    project_id,
                    option["text"],
                    option["status"],
                    SCHEMA_VERSION,
                    created_at,
                    now,
                ),
            )

        for block in data.get("deliverable_blocks", []):
            content = block.get("content", "")
            if not str(content).strip():
                raise SampleImportError(f"交付块 {block['id']} 必须包含实际正文")
            connection.execute(
                """
                INSERT INTO deliverable_blocks (
                    id, project_id, title, current_text, restriction,
                    delivery_status, current_version, schema_version,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'draft', 1, ?, ?, ?)
                """,
                (
                    block["id"],
                    project_id,
                    block["title"],
                    content,
                    block.get("restriction"),
                    SCHEMA_VERSION,
                    created_at,
                    now,
                ),
            )
            for claim_id in block.get("claim_ids", []):
                connection.execute(
                    "INSERT INTO deliverable_block_claims VALUES (?, ?)",
                    (block["id"], claim_id),
                )
            for finding_id in block.get("finding_ids", []):
                connection.execute(
                    "INSERT INTO deliverable_block_findings VALUES (?, ?)",
                    (block["id"], finding_id),
                )
            for option_id in block.get("option_ids", []):
                connection.execute(
                    "INSERT INTO deliverable_block_options VALUES (?, ?)",
                    (block["id"], option_id),
                )
            connection.execute(
                """
                INSERT INTO deliverable_block_revisions (
                    id, deliverable_block_id, version, body, origin, adopted,
                    review_decision_id, override_decision_id, created_at, schema_version
                ) VALUES (?, ?, 1, ?, 'snapshot', 1, NULL, NULL, ?, ?)
                """,
                (f"{block['id']}-v1", block["id"], content, now, SCHEMA_VERSION),
            )

        override = data.get("override_decision")
        if override:
            connection.execute(
                """
                INSERT INTO override_decisions (
                    id, project_id, deliverable_block_id, handling, reason,
                    review_trigger, target_version, created_at, schema_version, updated_at
                ) VALUES (?, ?, NULL, 'assumption', ?, ?, 1, ?, ?, ?)
                """,
                (
                    override["id"],
                    project_id,
                    override["action"],
                    override.get("review_trigger"),
                    now,
                    SCHEMA_VERSION,
                    now,
                ),
            )
    return project_id


def _optional_bool(value: object) -> int | None:
    if value is None:
        return None
    return int(bool(value))
