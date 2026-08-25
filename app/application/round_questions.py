from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.draft_suggestion import DraftSuggestionError
from app.application.update_brief import BriefUpdateError, update_brief
from app.application.capture_source import MANAGER_FEEDBACK_KIND
from app.application.create_project import PLACEHOLDER_TEXT
from app.application.source_snapshot import (
    SnapshotError,
    read_source_snapshot,
    snapshot_plain_text,
)
from app.projections.brief import build_brief_projection
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection
from app.templates.registry import TemplateError, load_template

MAX_QUESTIONS = 5
# 上限只是防跑飞，不是排版手段。短名（label）负责第一层好看，整句留全。
MAX_QUESTION_CHARS = 200
MAX_WHY_CHARS = 260
_SENTENCE_ENDS = "？。！?!"


def _trim_to_sentence(text: str, limit: int) -> str:
    """超长就切到最后一个句末，切不出完整句子就整句留着。

    从中间一刀切下去，账本里会留下一句半截话，经理看到的也是半截话。
    宁可长一点，也不写一句没说完的问题。
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(mark) for mark in _SENTENCE_ENDS)
    if cut >= limit // 3:
        return head[: cut + 1]
    return text


class RoundQuestionError(ValueError):
    pass


def draft_round_questions(
    repository: SqliteRepository,
    project_id: str,
    *,
    adapter: Any = None,
) -> dict[str, Any]:
    """按经理原话先拟本轮问题候选；不写入问题，不改稿，不改核验。"""
    if not repository.has_project(project_id):
        raise RoundQuestionError(f"项目 {project_id} 不存在")
    if adapter is None:
        from app.adapters.http_draft import resolve_draft_adapter

        adapter = resolve_draft_adapter()
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    brief = build_brief_projection(repository, project_id)
    decision = str(brief["brief"].get("decision_question") or "").strip()
    original = str(brief["brief"].get("original_context") or "").strip()
    if not decision and not original:
        raise RoundQuestionError("先写下这轮要决定什么，再拆问题。没有写入问题，也没有改稿。")
    existing = [
        item["question"]
        for item in brief["questions"]
        if item["status"] != DEFERRED_STATUS
        and int(item.get("round_index") or 1) == int(brief.get("current_round") or 1)
    ]
    archived = [
        item["question"]
        for item in brief["questions"]
        if int(item.get("round_index") or 1) < int(brief.get("current_round") or 1)
    ]
    hints = _template_hints(repository, project_id)
    current_round = int(brief.get("current_round") or 1)
    context: dict[str, Any] = {
        "task": "round_questions",
        "decision_question": decision,
        "original_context": original,
        "questions": existing,
        "archived_questions": archived,
        "template_hints": hints,
        "round_index": current_round,
        # 先立骨架再找料：把现有节名给模型，让它说清这条问题落在哪一节。
        "sections": _section_titles(repository, project_id),
    }
    if current_round > 1:
        # 第二轮起不再重拆经理原话，而是审阅上一轮：稿答到哪了、还差什么，
        # 再叠上经理这一轮的反馈。反馈是内部指示，不是证据。
        context["previous_sections"] = _previous_sections(repository, project_id)
        context["manager_feedback"] = _manager_feedback(repository, project_id)
    try:
        proposals = list(adapter.propose(context) or [])
    except DraftSuggestionError as error:
        raise RoundQuestionError(str(error)) from error
    except Exception as error:
        raise RoundQuestionError(str(error) or "模型没有给出本轮问题。") from error
    questions = _clean_proposals(proposals)
    if not questions:
        raise RoundQuestionError("模型没有给出本轮问题。没有写入问题，也没有改稿。")
    if _all_claim_flags(repository) != before_flags:
        raise RoundQuestionError("拆问题不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise RoundQuestionError("拆问题不得改写给经理的稿。")
    return {
        "questions": questions,
        "workbench": build_workbench_projection(repository, project_id),
        "confirmation": {
            "recorded": True,
            "record_kind": "draft_round_questions",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已拆出本轮问题候选。点收下才进左栏。核验未改，给经理的稿未改。",
        },
    }


def adopt_round_questions(
    repository: SqliteRepository,
    project_id: str,
    questions: Any,
) -> dict[str, Any]:
    """收下本轮问题候选；旧的这轮先不用，不删除对象，不改稿，不改核验。"""
    if not repository.has_project(project_id):
        raise RoundQuestionError(f"项目 {project_id} 不存在")
    cleaned = _clean_proposals(questions if isinstance(questions, list) else [])
    if not cleaned:
        raise RoundQuestionError("没有可收下的本轮问题。")
    brief = build_brief_projection(repository, project_id)
    previous_ids = [
        item["id"]
        for item in brief["questions"]
        if item["status"] != DEFERRED_STATUS
        and int(item.get("round_index") or 1) == int(brief.get("current_round") or 1)
    ]
    payload: list[dict[str, Any]] = [
        {"id": question_id, "status": DEFERRED_STATUS} for question_id in previous_ids
    ]
    # 节名对不上现有的任何一节就留空，绝不猜：猜错了人不会发现，
    # 而这条链子本来是为了让人知道材料该往哪儿放。
    by_title = _blocks_by_title(repository, project_id)
    for item in cleaned:
        payload.append(
            {
                "question": item["question"],
                "enough_for_now": item.get("enough_for_now"),
                "label": item.get("label"),
                "target_block_id": by_title.get(_norm(item.get("section"))),
                "status": "not_started",
            }
        )
    try:
        result = update_brief(repository, project_id, questions=payload)
    except BriefUpdateError as error:
        raise RoundQuestionError(str(error)) from error
    added = [
        item
        for item in result["brief_projection"]["questions"]
        if item["id"] not in previous_ids and item["status"] != DEFERRED_STATUS
    ]
    confirmation = dict(result["confirmation"])
    confirmation["message"] = "已收下本轮问题。原先那几条这轮先不用。核验未改，给经理的稿未改。"
    if int(brief.get("current_round") or 1) > 1:
        confirmation["message"] = (
            "已收下第 "
            + str(brief.get("current_round"))
            + " 轮问题。上一轮仍归档在下面。核验未改，给经理的稿未改。"
        )
    confirmation["record_kind"] = "adopt_round_questions"
    return {
        "question_ids": [item["id"] for item in added],
        "workbench": build_workbench_projection(repository, project_id),
        "brief_projection": result["brief_projection"],
        "confirmation": confirmation,
    }


def rename_research_question(
    repository: SqliteRepository,
    question_id: str,
    question: Any,
    *,
    label: Any = None,
) -> dict[str, Any]:
    """改本轮问题的措辞和短名；不删除对象，不改核验，也不改给经理的稿。"""
    text = str(question or "").strip()
    if not text:
        raise RoundQuestionError("本轮问题不能空着")
    with repository.connect() as connection:
        row = connection.execute(
            """
            SELECT q.project_id, q.status, q.round_index, p.current_round
            FROM research_questions q
            JOIN projects p ON p.id = q.project_id
            WHERE q.id = ?
            """,
            (question_id,),
        ).fetchone()
    if row is None:
        raise RoundQuestionError(f"本轮问题 {question_id} 不存在")
    if int(row["round_index"] or 1) != int(row["current_round"] or 1):
        raise RoundQuestionError("上一轮已经收口。本轮再拆或补一条。")
    if row["status"] == DEFERRED_STATUS:
        raise RoundQuestionError("这条这轮先不用。要点这轮再用，才能改。")
    try:
        result = update_brief(
            repository,
            str(row["project_id"]),
            questions=[_rename_payload(question_id, text, label)],
        )
    except BriefUpdateError as error:
        raise RoundQuestionError(str(error)) from error
    confirmation = dict(result["confirmation"])
    confirmation["message"] = "问题已改。核验未改，给经理的稿未改。"
    confirmation["record_kind"] = "rename_question"
    return {
        "question_id": question_id,
        "workbench": build_workbench_projection(repository, str(row["project_id"])),
        "brief_projection": result["brief_projection"],
        "confirmation": confirmation,
    }


def _rename_payload(question_id: str, text: str, label: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"id": question_id, "question": text}
    if label is not None:
        row["label"] = label
    return row


def _template_hints(repository: SqliteRepository, project_id: str) -> list[str]:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT template_key FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        return []
    try:
        template = load_template(str(row["template_key"]))
    except TemplateError:
        return []
    return [str(item).strip() for item in template.recommended_question_labels() if str(item).strip()]


def _clean_proposals(items: list[Any]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            question = item.strip()
            why = ""
            label = ""
            section = ""
        elif isinstance(item, dict):
            question = str(item.get("question") or item.get("text") or "").strip()
            why = str(item.get("enough_for_now") or item.get("why") or "").strip()
            label = str(item.get("label") or "").strip()
            section = str(item.get("section") or "").strip()
        else:
            continue
        question = " ".join(question.split())
        if not question or question in seen:
            continue
        question = _trim_to_sentence(question, MAX_QUESTION_CHARS).rstrip()
        why = _trim_to_sentence(why, MAX_WHY_CHARS).rstrip()
        seen.add(question)
        row = {"question": question}
        if why:
            row["enough_for_now"] = why
        if label:
            row["label"] = label
        if section:
            row["section"] = " ".join(section.split())
        cleaned.append(row)
        if len(cleaned) >= MAX_QUESTIONS:
            break
    return cleaned


def _all_claim_flags(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, verification_status FROM claims ORDER BY id"
        ).fetchall()
    return {str(row["id"]): str(row["verification_status"]) for row in rows}


def _all_block_texts(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, current_text FROM deliverable_blocks ORDER BY id"
        ).fetchall()
    return {str(row["id"]): str(row["current_text"]) for row in rows}


def _previous_sections(
    repository: SqliteRepository, project_id: str
) -> list[dict[str, str]]:
    """上一轮各节的标题和收下的正文，供模型审阅还差什么。只读，不改稿。"""
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT title, current_text FROM deliverable_blocks"
            " WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
    sections = []
    for row in rows:
        body = str(row["current_text"] or "").strip()
        if not body or body == PLACEHOLDER_TEXT:
            continue
        sections.append({"title": str(row["title"] or ""), "text": body})
    return sections


def _manager_feedback(repository: SqliteRepository, project_id: str) -> list[str]:
    """本轮收进来的经理反馈原话。"""
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM sources WHERE project_id = ? AND kind = ? ORDER BY rowid DESC",
            (project_id, MANAGER_FEEDBACK_KIND),
        ).fetchall()
    notes: list[str] = []
    for row in rows:
        try:
            body, content_type = read_source_snapshot(repository, str(row["id"]))
        except SnapshotError:
            continue
        note = snapshot_plain_text(body, content_type).strip()
        if note:
            notes.append(note)
    return notes


def draft_round_decision(
    repository: SqliteRepository,
    project_id: str,
    *,
    adapter: Any = None,
) -> dict[str, Any]:
    """第二轮起，先拟这一轮要决定什么。只出候选，人点收下才写进 Brief。"""
    if not repository.has_project(project_id):
        raise RoundQuestionError(f"项目 {project_id} 不存在")
    brief = build_brief_projection(repository, project_id)
    current_round = int(brief.get("current_round") or 1)
    if current_round < 2:
        raise RoundQuestionError(
            "第一轮要决定什么来自经理原话，自己写。第二轮起才由模型先拟。"
        )
    if adapter is None:
        from app.adapters.http_draft import resolve_draft_adapter

        adapter = resolve_draft_adapter()
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    context = {
        "task": "round_decision",
        "decision_question": str(brief["brief"].get("decision_question") or ""),
        "original_context": str(brief["brief"].get("original_context") or ""),
        "questions": [],
        "archived_questions": [
            item["question"]
            for item in brief["questions"]
            if int(item.get("round_index") or 1) < current_round
        ],
        "round_index": current_round,
        "previous_sections": _previous_sections(repository, project_id),
        "manager_feedback": _manager_feedback(repository, project_id),
    }
    try:
        proposals = list(adapter.propose(context) or [])
    except DraftSuggestionError as error:
        raise RoundQuestionError(str(error)) from error
    except Exception as error:
        raise RoundQuestionError(str(error) or "模型没有给出这一轮要决定什么。") from error
    decision = ""
    for item in proposals:
        if isinstance(item, dict):
            decision = " ".join(str(item.get("text") or "").split())
        elif isinstance(item, str):
            decision = " ".join(item.split())
        if decision:
            break
    if not decision:
        raise RoundQuestionError("模型没有给出这一轮要决定什么。没有改稿。")
    if _all_claim_flags(repository) != before_flags:
        raise RoundQuestionError("先拟这轮要决定什么不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise RoundQuestionError("先拟这轮要决定什么不得改写给经理的稿。")
    return {
        "decision": decision,
        "confirmation": {
            "recorded": True,
            "record_kind": "draft_round_decision",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已拟出这一轮要决定什么。点收下才写进本轮。核验未改，给经理的稿未改。",
        },
    }


def _section_titles(repository: SqliteRepository, project_id: str) -> list[str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT title FROM deliverable_blocks WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
    return [str(row["title"] or "").strip() for row in rows if str(row["title"] or "").strip()]


def _blocks_by_title(repository: SqliteRepository, project_id: str) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, title FROM deliverable_blocks WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        ).fetchall()
    mapping: dict[str, str] = {}
    for row in rows:
        key = _norm(row["title"])
        if key and key not in mapping:
            mapping[key] = str(row["id"])
    return mapping


def _norm(title: Any) -> str:
    """比节名时忽略空白和常见标点，别因为一个顿号就对不上。"""
    text = str(title or "")
    return "".join(ch for ch in text if not ch.isspace() and ch not in "、，,。.：:「」《》()（）")
