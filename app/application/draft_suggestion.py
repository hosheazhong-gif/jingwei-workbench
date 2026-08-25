from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.application.attach_finding import attach_finding_to_block
from app.application.attach_option import attach_option_to_block
from app.application.create_project import PLACEHOLDER_TEXT
from app.application.ids import allocate_prefixed_id
from app.application.review_block import ReviewError, propose_block_revision
from app.domain import ModelSuggestionKind, ModelSuggestionStatus
from app.projections.brief import build_brief_projection
from app.projections.paragraphs import ensure_paragraphs
from app.projections.report import build_report_projection, build_review_context
from app.projections.workbench import DEFERRED_STATUS

SUGGESTION_LIMITATION = (
    "模型先拟，没有可定位来源。不是证据，也不能写成内部稿。"
    "点「用这版」才会挂到本段。"
)
ALLOWED_KINDS = {item.value for item in ModelSuggestionKind}


class DraftSuggestionError(ValueError):
    pass


def draft_model_suggestions(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    adapter: Any = None,
) -> dict[str, Any]:
    """把模型先拟写入 ModelSuggestion；不改内部稿，也不改变主张核验状态。"""
    if adapter is None:
        from app.adapters.http_draft import resolve_draft_adapter

        adapter = resolve_draft_adapter()
    before_status = _all_claim_statuses(repository)
    try:
        before_draft = _block_text(repository, deliverable_block_id)
        review = build_review_context(repository, deliverable_block_id)
    except (DraftSuggestionError, KeyError) as error:
        raise DraftSuggestionError(str(error)) from error
    block = review["block"]
    context = {
        "block_id": block["id"],
        "title": block["title"],
        "claims": [
            {"text": item["text"]} for item in review["claims"]
        ],
    }
    proposals = list(adapter.propose(context) or [])
    if not proposals:
        raise DraftSuggestionError("模型没有给出可先拟的候选。")
    now = datetime.now(UTC).isoformat()
    created: list[str] = []
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT id, project_id, title, current_text FROM deliverable_blocks WHERE id = ?",
            (deliverable_block_id,),
        ).fetchone()
        if row is None:
            raise DraftSuggestionError(f"报告段落 {deliverable_block_id} 不存在")
        for item in proposals:
            kind = str(item.get("kind") or "").strip()
            text = str(item.get("text") or "").strip()
            if kind not in ALLOWED_KINDS:
                raise DraftSuggestionError("先拟只能是总判断或可试方向候选")
            if not text:
                raise DraftSuggestionError("先拟正文不能为空")
            suggestion_id = allocate_prefixed_id(connection, "model_suggestions", "MS")
            connection.execute(
                """
                INSERT INTO model_suggestions (
                    id, project_id, deliverable_block_id, kind, text, status,
                    adopted_finding_id, adopted_option_id, adapter_key, limitation,
                    schema_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
                """,
                (
                    suggestion_id,
                    row["project_id"],
                    deliverable_block_id,
                    kind,
                    text,
                    ModelSuggestionStatus.PENDING.value,
                    adapter.key,
                    SUGGESTION_LIMITATION,
                    SCHEMA_VERSION,
                    now,
                    now,
                ),
            )
            created.append(suggestion_id)

    _assert_unchanged(repository, deliverable_block_id, before_draft, before_status)
    after = build_review_context(repository, deliverable_block_id)
    report = build_report_projection(repository, block["project_id"])
    return {
        "suggestion_ids": created,
        "review_context": after,
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "draft_suggestion",
            "block_title": block["title"],
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已记下模型先拟。还没有挂到段落，也没有改左边正文。",
        },
    }


def draft_block_revision(
    repository: SqliteRepository,
    deliverable_block_id: str,
    *,
    adapter: Any = None,
    question_id: Any = None,
) -> dict[str, Any]:
    """模型先拟一版改稿候选；不替换 current_text，也不改变主张核验状态。"""
    if adapter is None:
        from app.adapters.http_draft import resolve_draft_adapter

        adapter = resolve_draft_adapter()
    before_status = _all_claim_statuses(repository)
    try:
        before_draft = _block_text(repository, deliverable_block_id)
        review = build_review_context(repository, deliverable_block_id)
    except (DraftSuggestionError, KeyError) as error:
        raise DraftSuggestionError(str(error)) from error
    block = review["block"]
    context = _revision_context(
        repository, block, review, focus_question_id=question_id
    )
    if context.get("focus_question") and not context.get("excerpts"):
        raise DraftSuggestionError(
            "先在右边打开这条问题的材料，把原话挂到这一节，再写。不能空写。"
        )
    proposals = list(adapter.propose(context) or [])
    texts = [
        str(item.get("text") or "").strip()
        for item in proposals
        if str(item.get("kind") or "").strip() == "revision"
    ]
    texts = [ensure_paragraphs(item) for item in texts if item]
    if not texts:
        raise DraftSuggestionError("模型没有给出可先拟的改稿。没有改内部稿。")
    try:
        result = propose_block_revision(
            repository, deliverable_block_id, body=texts[0]
        )
    except ReviewError as error:
        raise DraftSuggestionError(str(error)) from error
    _assert_unchanged(repository, deliverable_block_id, before_draft, before_status)
    confirmation = result["confirmation"]
    confirmation["message"] = (
        "已写出这一节候选。收下后才进给经理的稿。核验未改。"
    )
    confirmation["model_drafted"] = True
    return result


def adopt_model_suggestion(
    repository: SqliteRepository, suggestion_id: str
) -> dict[str, Any]:
    """人点用这版后，才把候选挂成 Finding 或 Option；仍不改内部稿和核验。"""
    suggestion = _require_pending(repository, suggestion_id)
    before_status = _all_claim_statuses(repository)
    before_draft = _block_text(repository, suggestion["deliverable_block_id"])
    if suggestion["kind"] == ModelSuggestionKind.FINDING.value:
        attached = attach_finding_to_block(
            repository,
            suggestion["deliverable_block_id"],
            text=suggestion["text"],
        )
        finding_id = attached["finding_id"]
        option_id = None
        kind_label = "总判断"
    else:
        attached = attach_option_to_block(
            repository,
            suggestion["deliverable_block_id"],
            text=suggestion["text"],
        )
        finding_id = None
        option_id = attached["option_id"]
        kind_label = "可试方向"
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE model_suggestions
            SET status = ?, adopted_finding_id = ?, adopted_option_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                ModelSuggestionStatus.ADOPTED.value,
                finding_id,
                option_id,
                now,
                suggestion_id,
            ),
        )
    _assert_unchanged(
        repository, suggestion["deliverable_block_id"], before_draft, before_status
    )
    review = build_review_context(repository, suggestion["deliverable_block_id"])
    return {
        "suggestion_id": suggestion_id,
        "finding_id": finding_id,
        "option_id": option_id,
        "review_context": review,
        "report": attached["report"],
        "confirmation": {
            "recorded": True,
            "record_kind": "adopt_suggestion",
            "block_title": review["block"]["title"],
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": f"已用这版{kind_label}。未改核验，也未改左边正文。",
        },
    }


def dismiss_model_suggestion(
    repository: SqliteRepository, suggestion_id: str
) -> dict[str, Any]:
    suggestion = _require_pending(repository, suggestion_id)
    before_status = _all_claim_statuses(repository)
    before_draft = _block_text(repository, suggestion["deliverable_block_id"])
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        connection.execute(
            """
            UPDATE model_suggestions
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (ModelSuggestionStatus.DISMISSED.value, now, suggestion_id),
        )
    _assert_unchanged(
        repository, suggestion["deliverable_block_id"], before_draft, before_status
    )
    review = build_review_context(repository, suggestion["deliverable_block_id"])
    report = build_report_projection(repository, suggestion["project_id"])
    return {
        "suggestion_id": suggestion_id,
        "review_context": review,
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "dismiss_suggestion",
            "block_title": review["block"]["title"],
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "这版先不用。未改核验，也未改左边正文。",
        },
    }


def _revision_context(
    repository: SqliteRepository,
    block: dict[str, Any],
    review: dict[str, Any],
    *,
    focus_question_id: Any = None,
) -> dict[str, Any]:
    excerpts: list[str] = []
    attributions: list[str] = []
    claims = []
    for item in review.get("claims") or []:
        claims.append({"text": item.get("text")})
        attribution = _excerpt_attribution(item)
        for evidence in item.get("evidence") or []:
            excerpt = str(evidence.get("excerpt") or "").strip()
            if excerpt and excerpt not in excerpts:
                excerpts.append(excerpt)
                attributions.append(attribution)
    limit = _excerpt_char_limit(excerpts)
    excerpt_lines = [
        _label_excerpt(excerpt, attributions[index], limit)
        for index, excerpt in enumerate(excerpts)
    ]
    decision = ""
    original_context = ""
    questions: list[str] = []
    focus = ""
    enough_for_now = ""
    materials: list[str] = []
    other_excerpts: list[str] = []
    other_excerpt_lines: list[str] = []
    current = str(block.get("current_text") or "")
    placeholder = current.strip() == PLACEHOLDER_TEXT
    try:
        brief = build_brief_projection(repository, block["project_id"])
        decision = str(brief["brief"].get("decision_question") or "")
        original_context = str(brief["brief"].get("original_context") or "")
        active = [
            row
            for row in brief["questions"]
            if row["status"] != DEFERRED_STATUS
            and int(row.get("round_index") or 1) == int(brief.get("current_round") or 1)
        ]
        questions = [row["question"] for row in active]
        key = str(focus_question_id or "").strip()
        if key:
            match = next(
                (row for row in brief["questions"] if row["id"] == key),
                None,
            )
            if match is None:
                raise DraftSuggestionError("本轮问题不在这题里")
            if int(match.get("round_index") or 1) != int(brief.get("current_round") or 1):
                raise DraftSuggestionError("上一轮已经收口。换本轮问题再写。")
            if match["status"] == DEFERRED_STATUS:
                raise DraftSuggestionError(
                    "这条这轮先不用。要点这轮再用，才能带着它写。"
                )
            focus = str(match["question"] or "").strip()
            enough_for_now = str(match.get("enough_for_now") or "").strip()
            questions = [focus] + [item for item in questions if item != focus]
        materials, extra_excerpts, extra_lines = _project_material_context(
            repository, block["project_id"], focus_question_id=key or None
        )
        hung = set(excerpts)
        other_excerpts = [
            excerpt for excerpt in extra_excerpts if excerpt not in hung
        ]
        other_excerpt_lines = [
            line
            for excerpt, line in extra_lines
            if excerpt not in hung
        ]
    except KeyError:
        pass
    return {
        "task": "revision",
        "block_id": block["id"],
        "title": block["title"],
        "current_text": current,
        "placeholder": placeholder,
        "original_context": original_context,
        "decision_question": decision,
        "focus_question": focus,
        "enough_for_now": enough_for_now,
        "questions": questions,
        "materials": materials,
        "excerpts": excerpts,
        "excerpt_lines": excerpt_lines,
        "other_excerpts": other_excerpts,
        "other_excerpt_lines": other_excerpt_lines,
        "claims": claims,
    }


def _project_material_context(
    repository: SqliteRepository,
    project_id: str,
    *,
    focus_question_id: str | None = None,
) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    with repository.connect() as connection:
        source_rows = connection.execute(
            """
            SELECT title, limitation, kind, research_question_id FROM sources
            WHERE project_id = ?
            ORDER BY rowid
            """,
            (project_id,),
        ).fetchall()
        excerpt_rows = connection.execute(
            """
            SELECT e.excerpt, c.provenance_scope, c.independently_verified,
                   c.delivery_rule, s.kind, s.title, s.limitation
            FROM evidence_excerpts e
            JOIN claim_evidence ce ON ce.evidence_excerpt_id = e.id
            JOIN claims c ON c.id = ce.claim_id
            LEFT JOIN sources s ON s.id = c.source_id
            WHERE c.project_id = ?
            ORDER BY e.rowid
            """,
            (project_id,),
        ).fetchall()
    materials = []
    for row in source_rows:
        title = str(row["title"] or "").strip()
        if not title:
            continue
        tagged = str(row["research_question_id"] or "").strip()
        if focus_question_id and tagged and tagged != focus_question_id:
            continue
        limitation = str(row["limitation"] or "").strip()
        kind = str(row["kind"] or "").strip()
        if kind == "web_page":
            title = title + "（公开网页，不是客户提供）"
        if "宏观" in limitation:
            title = title + "（宏观，不单独证明项目需求）"
        materials.append(title)
    excerpts: list[str] = []
    lines: list[tuple[str, str]] = []
    seen: set[str] = set()
    for row in excerpt_rows:
        excerpt = str(row["excerpt"] or "").strip()
        if not excerpt or excerpt in seen:
            continue
        seen.add(excerpt)
        excerpts.append(excerpt)
        lines.append(
            (
                excerpt,
                _label_excerpt(
                    excerpt,
                    _excerpt_attribution(
                        {
                            "provenance_scope": row["provenance_scope"],
                            "independently_verified": row["independently_verified"],
                            "delivery_rule": row["delivery_rule"],
                            "source": {
                                "kind": row["kind"],
                                "title": row["title"],
                                "limitation": row["limitation"],
                            },
                        }
                    ),
                ),
            )
        )
    return materials, excerpts, lines


def _excerpt_attribution(claim: dict[str, Any]) -> str:
    source = claim.get("source") or {}
    title = str(source.get("title") or "").strip()
    kind = str(source.get("kind") or "").strip()
    limitation = str(source.get("limitation") or "")
    delivery = str(claim.get("delivery_rule") or "")
    scope = str(claim.get("provenance_scope") or "")
    client = scope == "client_provided" or "客户提供" in delivery
    feedback = scope == "manager_feedback" or "经理反馈" in delivery
    verified = bool(claim.get("independently_verified"))
    if client:
        origin = "客户提供，口径待补，不等于外部独立核实"
    elif feedback:
        origin = "经理反馈，内部指示，不是客户口径，也不是外部证据"
    elif kind == "web_page":
        origin = "公开网页，不是客户提供"
    elif kind in {"local_file", "file"}:
        origin = "本机文件，不是客户提供"
    else:
        origin = "已入库材料，不是客户提供"
    origin += "，已独立核实" if verified else "，未独立核实"
    if "宏观" in limitation:
        origin += "；宏观材料不单独证明项目需求"
    if title:
        return origin + "。出处：" + title
    return origin


# 人可以把整篇文字粘成一条原话。库里原样保存，但发给模型的那一份要有个总量。
# 按总预算截，不按每条截：只挂一条长材料时它应当整条送进去——稿要写细正靠它。
PROMPT_EXCERPT_BUDGET_CHARS = 12000
MIN_PROMPT_EXCERPT_CHARS = 400


def _excerpt_char_limit(excerpts: list[str]) -> int | None:
    """这一批原话每条最多送多少字；总量够就不截。"""
    if not excerpts:
        return None
    total = sum(len(item or "") for item in excerpts)
    if total <= PROMPT_EXCERPT_BUDGET_CHARS:
        return None
    return max(MIN_PROMPT_EXCERPT_CHARS, PROMPT_EXCERPT_BUDGET_CHARS // len(excerpts))


def _label_excerpt(
    excerpt: str, attribution: str, limit: int | None = None
) -> str:
    body = str(excerpt or "")
    if limit is not None and len(body) > limit:
        body = (
            body[:limit]
            + "…（这条原话共 "
            + str(len(body))
            + " 字，这里只给出前 "
            + str(limit)
            + " 字；后面的内容不得当作已知）"
        )
    return "「" + body + "」——" + attribution


def _require_pending(repository: SqliteRepository, suggestion_id: str) -> dict[str, Any]:
    key = str(suggestion_id or "").strip()
    if not key:
        raise DraftSuggestionError("先拟候选不能为空")
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT * FROM model_suggestions WHERE id = ?",
            (key,),
        ).fetchone()
    if row is None:
        raise DraftSuggestionError(f"先拟候选 {key} 不存在")
    payload = dict(row)
    if payload["status"] != ModelSuggestionStatus.PENDING.value:
        raise DraftSuggestionError("只能采用或放下尚未用上的先拟")
    return payload


def _block_text(repository: SqliteRepository, block_id: str) -> str:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
    if row is None:
        raise DraftSuggestionError(f"报告段落 {block_id} 不存在")
    return row["current_text"]


def _all_claim_statuses(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, verification_status FROM claims"
        ).fetchall()
    return {row["id"]: row["verification_status"] for row in rows}


def _assert_unchanged(
    repository: SqliteRepository,
    block_id: str,
    before_draft: str,
    before_status: dict[str, str],
) -> None:
    if _block_text(repository, block_id) != before_draft:
        raise DraftSuggestionError("先拟不得改写内部稿")
    if _all_claim_statuses(repository) != before_status:
        raise DraftSuggestionError("先拟不得改变主张核验状态")
