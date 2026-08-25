from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.ids import allocate_prefixed_id
from app.projections.brief import QUESTION_STATUSES, build_brief_projection
from app.projections.report import build_report_projection
from app.templates.registry import DEFAULT_TEMPLATE_KEY, TemplateError, load_template

DEFAULT_DELIVERABLE = "内部研究初稿"
DEFAULT_STAGE = "intake"
DEFAULT_DECISION_GATE = "brainstorm_ready"
PLACEHOLDER_TEXT = "这一节还没写。"
PLACEHOLDER_RESTRICTION = "尚无来源与主张；不能当作已核实结论。"


class ProjectCreateError(ValueError):
    pass


def create_project(
    repository: SqliteRepository,
    *,
    name: Any,
    original_context: Any,
    decision_question: str | None = None,
    deliverable: str | None = None,
    not_a_final_client_recommendation: bool = True,
    questions: list[Any] | None = None,
    template_key: str | None = None,
) -> dict[str, Any]:
    """新建空白题目：写入 Project 与 Brief，不覆盖已有项目，也不生成内部稿。"""
    project_name = _required_text(name, "题目名称")
    context = _required_text(original_context, "历史情境或一句话任务")
    decision = _optional_text(decision_question) or context
    delivery = _optional_text(deliverable) or DEFAULT_DELIVERABLE
    key = _optional_text(template_key) or DEFAULT_TEMPLATE_KEY
    try:
        template = load_template(key)
    except TemplateError as error:
        raise ProjectCreateError(str(error)) from error

    question_rows = _normalize_questions(questions)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        project_id = allocate_prefixed_id(connection, "projects", "P")
        brief_id = allocate_prefixed_id(connection, "briefs", "B")
        connection.execute(
            """
            INSERT INTO projects (
                id, name, template_key, execution_strategy_key, stage, decision_gate,
                schema_version, created_at, updated_at, current_round
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                project_id,
                project_name,
                template.key,
                template.execution_strategy_key,
                DEFAULT_STAGE,
                DEFAULT_DECISION_GATE,
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO briefs (
                id, project_id, original_context, decision_question, deliverable,
                not_a_final_client_recommendation, schema_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                brief_id,
                project_id,
                context,
                decision,
                delivery,
                int(bool(not_a_final_client_recommendation)),
                SCHEMA_VERSION,
                now,
                now,
            ),
        )
        for item in question_rows:
            question_id = allocate_prefixed_id(connection, "research_questions", "RQ")
            connection.execute(
                """
                INSERT INTO research_questions (
                    id, project_id, question, enough_for_now, status,
                    schema_version, created_at, updated_at, round_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    question_id,
                    project_id,
                    item["question"],
                    item["enough_for_now"],
                    item["status"],
                    SCHEMA_VERSION,
                    now,
                    now,
                ),
            )
        _insert_review_shell(connection, project_id, template, now)

    report = build_report_projection(repository, project_id)
    brief = build_brief_projection(repository, project_id)
    return {
        "project_id": project_id,
        "brief_id": brief_id,
        "report": report,
        "brief_projection": brief,
        "confirmation": {
            "recorded": True,
            "created_new_project": True,
            "did_not_overwrite_existing": True,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "已新建题目。放入一条占位内部稿，不含生成结论，也未改写其他项目。",
        },
    }


def ensure_review_shell(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    """空题目补一条占位内部稿，供审查侧栏打开；不覆盖已有段落，也不生成结论。"""
    before_status = _claim_statuses(repository, project_id)
    before_draft = _block_texts(repository, project_id)
    with repository.connect() as connection:
        project = connection.execute(
            "SELECT id, template_key FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if project is None:
            raise ProjectCreateError(f"项目 {project_id} 不存在")
        existing = connection.execute(
            """
            SELECT id FROM deliverable_blocks
            WHERE project_id = ? ORDER BY rowid
            """,
            (project_id,),
        ).fetchall()
    if existing:
        report = build_report_projection(repository, project_id)
        return {
            "created": False,
            "block_id": existing[0]["id"],
            "report": report,
            "confirmation": {
                "recorded": False,
                "verification_status_unchanged": True,
                "current_text_unchanged": True,
                "message": "已有段落，未再放入占位壳。",
            },
        }
    try:
        template = load_template(project["template_key"])
    except TemplateError as error:
        raise ProjectCreateError(str(error)) from error
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        block_id = _insert_review_shell(connection, project_id, template, now)
    after_status = _claim_statuses(repository, project_id)
    if before_status != after_status:
        raise ProjectCreateError("放入占位内部稿不得改变主张核验状态")
    if before_draft:
        raise ProjectCreateError("放入占位内部稿不得改写已有内部稿")
    report = build_report_projection(repository, project_id)
    return {
        "created": True,
        "block_id": block_id,
        "report": report,
        "confirmation": {
            "recorded": True,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "已放入一条占位内部稿，不含生成结论。证据核验状态未改变。",
        },
    }


def _insert_review_shell(
    connection: Any, project_id: str, template: Any, now: str
) -> str:
    labels = template.natural_language_labels()
    title = str(labels.get("deliverable_block") or "未命名的一节")
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
            title,
            PLACEHOLDER_TEXT,
            PLACEHOLDER_RESTRICTION,
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
        (f"{block_id}-v1", block_id, PLACEHOLDER_TEXT, now, SCHEMA_VERSION),
    )
    return block_id


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


def _normalize_questions(
    raw: list[Any] | None,
) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ProjectCreateError("questions 必须是列表")
    rows: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            rows.append(
                {
                    "question": text,
                    "enough_for_now": None,
                    "status": "not_started",
                }
            )
            continue
        if not isinstance(item, dict):
            raise ProjectCreateError("questions 项必须是字符串或对象")
        question = _required_text(item.get("question"), "研究问题")
        status = str(item.get("status") or "not_started")
        if status not in QUESTION_STATUSES:
            raise ProjectCreateError(f"不支持的问题状态 {status}")
        enough = item.get("enough_for_now")
        rows.append(
            {
                "question": question,
                "enough_for_now": None if enough is None else str(enough),
                "status": status,
            }
        )
    return rows


def _required_text(value: Any, label: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ProjectCreateError(f"{label}不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
