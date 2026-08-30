from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.candidate_source import CandidateSourceError, capture_web_candidate
from app.projections.brief import build_brief_projection
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection

MAX_QUERIES = 3
MAX_NEW_CANDIDATES = 8
MAX_QUERY_CHARS = 80
MAX_NOTE_CHARS = 180

# 第一条出结果的检索词之后就不再搜后面几条——默认行为，省额度也省噪声。
# 做 A/B 对照时要能临时关掉：不关的话固定流程永远只跑一条词，
# 跟多轮代理比出来的差距有一半是"跑了几条词"，不是"换不换词"。
# 只有 scripts/ab_search.py 会改它，改完立刻还原。
STOP_AFTER_FIRST_PRODUCTIVE_QUERY = True


class SearchMaterialsError(ValueError):
    pass


def search_project_materials(
    repository: SqliteRepository,
    project_id: str,
    *,
    question_id: str | None = None,
    search_adapter: Any = None,
    draft_adapter: Any = None,
) -> dict[str, Any]:
    """按本轮问题搜公开网页，写入 CandidateSource。不升为来源，不改稿，不改核验。"""
    if not repository.has_project(project_id):
        raise SearchMaterialsError(f"项目 {project_id} 不存在")
    question_id = str(question_id).strip() if question_id else None
    if not question_id:
        question_id = None
    if search_adapter is None:
        from app.adapters.http_search import resolve_search_adapter

        try:
            search_adapter = resolve_search_adapter()
        except Exception as error:
            raise SearchMaterialsError(str(error)) from error
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    before_sources = repository.list_source_ids(project_id)
    brief = build_brief_projection(repository, project_id)
    current = int(brief.get("current_round") or 1)
    active = [
        item
        for item in brief["questions"]
        if item["status"] != DEFERRED_STATUS
        and int(item.get("round_index") or 1) == current
    ]
    if active and not question_id:
        raise SearchMaterialsError(
            "先点开左边要搜的那条问题，再搜。没有写入候选，也没有改稿。"
        )
    if question_id:
        _focus_question(brief, question_id)
    queries = _search_queries(brief, question_id=question_id, draft_adapter=draft_adapter)
    if not queries:
        raise SearchMaterialsError("先写下这轮要决定什么，或补一条本轮问题，再搜。没有写入候选，也没有改稿。")
    existing = _existing_urls(repository, project_id)
    added: list[dict[str, Any]] = []
    skipped = 0
    for query in queries:
        if len(added) >= MAX_NEW_CANDIDATES:
            break
        try:
            hits = list(search_adapter.search(query) or [])
        except SearchMaterialsError:
            raise
        except Exception as error:
            message = str(error).strip() or "没搜到。"
            if queries and "检索：" not in message:
                message = message.rstrip("。") + "。检索：" + "；".join(queries)
            if "没有写入候选" not in message:
                message = message.rstrip("。") + "。没有写入候选，也没有改稿。"
            raise SearchMaterialsError(message) from error
        for hit in hits:
            if len(added) >= MAX_NEW_CANDIDATES:
                break
            if not isinstance(hit, dict):
                continue
            url = str(hit.get("url") or "").strip()
            if not url or url in existing:
                skipped += 1
                continue
            title = str(hit.get("title") or "").strip() or None
            snippet = str(hit.get("snippet") or "").strip()
            note = _candidate_note(query, snippet)
            try:
                captured = capture_web_candidate(
                    repository,
                    project_id,
                    url=url,
                    title=title,
                    note=note,
                    question_id=question_id,
                )
            except CandidateSourceError as error:
                if "已在候选列表" in str(error) or "必须是 http" in str(error):
                    skipped += 1
                    existing.add(url)
                    continue
                raise SearchMaterialsError(str(error)) from error
            added.append(captured["candidate"])
            existing.add(url)
        if added and STOP_AFTER_FIRST_PRODUCTIVE_QUERY:
            break

    _assert_unchanged(
        repository,
        project_id,
        before_flags,
        before_drafts,
        before_sources,
    )
    workbench = build_workbench_projection(repository, project_id)
    return {
        "project_id": project_id,
        "queries": queries,
        "added": added,
        "added_count": len(added),
        "skipped_count": skipped,
        "workbench": workbench,
        "confirmation": {
            "recorded": True,
            "record_kind": "search_candidates",
            "verification_status_changed": False,
            "deliverable_changed": False,
            "source_created": False,
            "message": _confirmation_message(len(added), skipped, queries),
        },
    }


def _search_queries(
    brief: dict[str, Any],
    *,
    question_id: str | None,
    draft_adapter: Any,
) -> list[str]:
    heuristic = _heuristic_queries(brief, question_id=question_id)
    if draft_adapter is None:
        return heuristic
    try:
        proposals = list(
            draft_adapter.propose(
                {
                    "task": "search_queries",
                    "decision_question": brief["brief"]["decision_question"],
                    "original_context": brief["brief"]["original_context"],
                    "questions": [
                        item["question"]
                        for item in brief["questions"]
                        if item["status"] != DEFERRED_STATUS
                        and int(item.get("round_index") or 1)
                        == int(brief.get("current_round") or 1)
                    ],
                    "focus_question": _focus_question(brief, question_id),
                }
            )
            or []
        )
    except Exception:
        return heuristic
    queries: list[str] = []
    seen: set[str] = set()
    for item in proposals:
        if not isinstance(item, dict):
            continue
        text = _clean_query(item.get("text"))
        if not text or text in seen:
            continue
        queries.append(text)
        seen.add(text)
        if len(queries) >= MAX_QUERIES:
            break
    return queries or heuristic


def _heuristic_queries(brief: dict[str, Any], *, question_id: str | None) -> list[str]:
    queries: list[str] = []
    seen: set[str] = set()
    focus = _clean_query(_focus_question(brief, question_id))
    if focus:
        queries.append(focus)
        seen.add(focus)
    for item in brief["questions"]:
        if item["status"] == DEFERRED_STATUS:
            continue
        if int(item.get("round_index") or 1) != int(brief.get("current_round") or 1):
            continue
        text = _clean_query(item.get("question"))
        if not text or text in seen:
            continue
        queries.append(text)
        seen.add(text)
        if len(queries) >= MAX_QUERIES:
            return queries
    decision = _clean_query(brief["brief"].get("decision_question"))
    if decision and decision not in seen and len(queries) < MAX_QUERIES:
        queries.append(decision)
        seen.add(decision)
    context = _clean_query(brief["brief"].get("original_context"))
    if context and context not in seen and len(queries) < MAX_QUERIES:
        queries.append(context)
    return queries


def _focus_question(brief: dict[str, Any], question_id: str | None) -> str | None:
    if not question_id:
        return None
    current = int(brief.get("current_round") or 1)
    for item in brief["questions"]:
        if item["id"] == question_id:
            if int(item.get("round_index") or 1) != current:
                raise SearchMaterialsError(
                    "上一轮已经收口。换本轮问题再搜。没有写入候选，也没有改稿。"
                )
            if item["status"] == DEFERRED_STATUS:
                raise SearchMaterialsError("这条本轮问题这轮先不用，换一条再搜。没有写入候选，也没有改稿。")
            return item["question"]
    raise SearchMaterialsError("没有这条本轮问题。没有写入候选，也没有改稿。")


def _clean_query(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if "http://" in lowered or "https://" in lowered or "www." in lowered:
        return None
    text = " ".join(text.split())
    if len(text) > MAX_QUERY_CHARS:
        text = text[:MAX_QUERY_CHARS].rstrip()
    return text or None


def _candidate_note(query: str, snippet: str) -> str:
    parts = ["按本轮问题搜到，尚未打开。检索：" + query]
    if snippet:
        parts.append(snippet)
    note = " ".join(parts)
    if len(note) > MAX_NOTE_CHARS:
        return note[:MAX_NOTE_CHARS].rstrip()
    return note


def _confirmation_message(added: int, skipped: int, queries: list[str]) -> str:
    if added:
        return (
            "搜到 "
            + str(added)
            + " 条候选。打开后才能当依据。检索："
            + "；".join(queries)
        )
    if skipped:
        return "搜到的链接已经在匣子里。打开后才能当依据。"
    return "这次没搜到可打开的链接。没有改稿，也没有写成依据。"


def _existing_urls(repository: SqliteRepository, project_id: str) -> set[str]:
    # 已排除（这轮不用）的链接也算「见过」：否则再搜一次会把同一条网址当新
    # 候选重新收进来，「这轮不用的」抽屉里就会摆出两条标题相同、状态各自
    # 独立的旧候选，看着像哪条被换掉了。排除只是收起来，不是没见过。
    with repository.connect() as connection:
        candidate_rows = connection.execute(
            """
            SELECT url FROM candidate_sources
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
        source_rows = connection.execute(
            """
            SELECT original_url FROM sources
            WHERE project_id = ? AND original_url IS NOT NULL AND original_url != ''
            """,
            (project_id,),
        ).fetchall()
    urls = {str(row["url"]) for row in candidate_rows}
    urls.update(str(row["original_url"]) for row in source_rows)
    return urls


def _all_claim_flags(repository: SqliteRepository) -> dict[str, tuple[Any, Any]]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, verification_status, independently_verified FROM claims"
        ).fetchall()
    return {
        row["id"]: (row["verification_status"], row["independently_verified"])
        for row in rows
    }


def _all_block_texts(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, current_text FROM deliverable_blocks"
        ).fetchall()
    return {row["id"]: row["current_text"] for row in rows}


def _assert_unchanged(
    repository: SqliteRepository,
    project_id: str,
    before_flags: dict[str, tuple[Any, Any]],
    before_drafts: dict[str, str],
    before_sources: list[str],
) -> None:
    if _all_claim_flags(repository) != before_flags:
        raise SearchMaterialsError("按问题搜不得改变主张核验状态")
    if _all_block_texts(repository) != before_drafts:
        raise SearchMaterialsError("按问题搜不得改写给经理的稿")
    if repository.list_source_ids(project_id) != before_sources:
        raise SearchMaterialsError("按问题搜不得把链接升为来源")
