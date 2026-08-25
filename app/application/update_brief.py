from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.ids import allocate_prefixed_id
from app.projections.brief import QUESTION_STATUSES, build_brief_projection


class BriefUpdateError(ValueError):
    pass


def update_brief(
    repository: SqliteRepository,
    project_id: str,
    *,
    original_context: str | None = None,
    decision_question: str | None = None,
    deliverable: str | None = None,
    name: str | None = None,
    not_a_final_client_recommendation: bool | None = None,
    questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """更新已有任务边界；不改变 Claim 核验状态，也不改写内部稿。"""
    before_status = _claim_statuses(repository, project_id)
    before_draft = _block_texts(repository, project_id)
    try:
        current = build_brief_projection(repository, project_id)
    except KeyError as error:
        raise BriefUpdateError(str(error)) from error

    brief = current["brief"]
    new_context = _required_text(
        original_context, brief["original_context"], "历史情境或原始委托"
    )
    new_decision = _required_text(
        decision_question, brief["decision_question"], "当前需要支持的决策"
    )
    new_deliverable = _required_text(deliverable, brief["deliverable"], "本轮交付")
    new_name = current["project"]["name"]
    if name is not None:
        new_name = _required_text(name, new_name, "题目名称")
    new_flag = brief["not_a_final_client_recommendation"]
    if not_a_final_client_recommendation is not None:
        new_flag = bool(not_a_final_client_recommendation)

    if questions is not None and not isinstance(questions, list):
        raise BriefUpdateError("questions 必须是列表")
    existing = {item["id"]: item for item in current["questions"]}
    updates: list[dict[str, Any]] = []
    inserts: list[dict[str, Any]] = []
    for item in questions or []:
        if not isinstance(item, dict):
            raise BriefUpdateError("questions 项必须是对象")
        question_id = str(item.get("id") or "").strip()
        if not question_id:
            status = str(item.get("status") or "not_started")
            if status not in QUESTION_STATUSES:
                raise BriefUpdateError(f"不支持的问题状态 {status}")
            inserts.append(
                {
                    "question": _required_text(
                        "" if item.get("question") is None else item.get("question"),
                        "",
                        "研究问题",
                    ),
                    "enough_for_now": item.get("enough_for_now"),
                    "label": _clean_label(item.get("label")),
                    "target_block_id": item.get("target_block_id"),
                    "status": status,
                }
            )
            continue
        if question_id not in existing:
            raise BriefUpdateError(f"研究问题 {question_id} 不在当前项目")
        merged = dict(existing[question_id])
        if "question" in item:
            merged["question"] = _required_text(
                item.get("question"), merged["question"], "研究问题"
            )
        if "enough_for_now" in item:
            merged["enough_for_now"] = item.get("enough_for_now")
        if "label" in item:
            merged["label"] = _clean_label(item.get("label"))
        if "target_block_id" in item:
            merged["target_block_id"] = item.get("target_block_id")
        if "status" in item:
            status = str(item.get("status") or "")
            if status not in QUESTION_STATUSES:
                raise BriefUpdateError(f"不支持的问题状态 {status}")
            merged["status"] = status
        updates.append(merged)

    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        current_round = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        round_index = int((current_round["current_round"] if current_round else 1) or 1)
        connection.execute(
            """
            UPDATE briefs
            SET original_context = ?, decision_question = ?, deliverable = ?,
                not_a_final_client_recommendation = ?, updated_at = ?
            WHERE project_id = ?
            """,
            (
                new_context,
                new_decision,
                new_deliverable,
                int(new_flag),
                now,
                project_id,
            ),
        )
        connection.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
            (new_name, now, project_id),
        )
        for item in updates:
            connection.execute(
                """
                UPDATE research_questions
                SET question = ?, enough_for_now = ?, status = ?, label = ?,
                    target_block_id = ?, updated_at = ?
                WHERE id = ? AND project_id = ?
                """,
                (
                    item["question"],
                    item["enough_for_now"],
                    item["status"],
                    item.get("label"),
                    item.get("target_block_id"),
                    now,
                    item["id"],
                    project_id,
                ),
            )
        for item in inserts:
            question_id = allocate_prefixed_id(connection, "research_questions", "RQ")
            connection.execute(
                """
                INSERT INTO research_questions (
                    id, project_id, question, enough_for_now, status, label,
                    target_block_id, schema_version, created_at, updated_at,
                    round_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    question_id,
                    project_id,
                    item["question"],
                    item["enough_for_now"],
                    item["status"],
                    item.get("label"),
                    item.get("target_block_id"),
                    SCHEMA_VERSION,
                    now,
                    now,
                    round_index,
                ),
            )

    after_status = _claim_statuses(repository, project_id)
    after_draft = _block_texts(repository, project_id)
    if before_status != after_status or before_draft != after_draft:
        raise BriefUpdateError("更新任务边界不得改变主张核验状态或内部稿")

    projection = build_brief_projection(repository, project_id)
    return {
        "brief_projection": projection,
        "confirmation": {
            "recorded": True,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "任务边界已更新。证据核验状态未改变，内部稿未改写。",
        },
    }


MAX_QUESTION_LABEL_CHARS = 14


def _clean_label(raw: Any) -> str | None:
    """短名只是第一层看的名字，空着就空着，不替人编。"""
    label = " ".join(str(raw or "").split())
    if not label:
        return None
    if len(label) > MAX_QUESTION_LABEL_CHARS:
        label = label[:MAX_QUESTION_LABEL_CHARS].rstrip()
    return label or None


def _required_text(incoming: Any, fallback: str, label: str) -> str:
    if incoming is None:
        return fallback
    text = str(incoming).strip()
    if not text:
        raise BriefUpdateError(f"{label}不能为空")
    return text


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
