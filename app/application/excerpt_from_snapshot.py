from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.attach_claim import ClaimAttachError, attach_claim_to_block
from app.application.draft_suggestion import DraftSuggestionError
from app.application.source_snapshot import (
    MIN_SNAPSHOT_BODY_CHARS,
    SnapshotError,
    read_source_snapshot,
    snapshot_plain_text,
)
from app.projections.brief import build_brief_projection
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection

MAX_SNAPSHOT_CHARS = 8000
MAX_EXCERPTS = 5
MAX_EXCERPT_CHARS = 180
# 与卡片上「能不能扒原话」用同一个下限，避免界面说能扒、后端却收不下。
MIN_EXCERPT_CHARS = MIN_SNAPSHOT_BODY_CHARS


class ExcerptFromSnapshotError(ValueError):
    pass


def draft_snapshot_excerpts(
    repository: SqliteRepository,
    source_id: str,
    *,
    question_id: Any = None,
    deliverable_block_id: Any = None,
    adapter: Any = None,
) -> dict[str, Any]:
    """从快照先拟原话候选。不写入摘录，不改稿，不改核验。"""
    source = repository.get_source(source_id)
    if source is None:
        raise ExcerptFromSnapshotError(f"来源 {source_id} 不存在")
    project_id = str(source["project_id"])
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    try:
        body, content_type = read_source_snapshot(repository, source_id)
    except SnapshotError as error:
        raise ExcerptFromSnapshotError(str(error)) from error
    snapshot = snapshot_plain_text(body, content_type)
    if not snapshot:
        raise ExcerptFromSnapshotError(
            "这份快照没有可抽出的正文，没法从里面扒原话。没有记下原话，也没有改稿。"
        )
    brief = build_brief_projection(repository, project_id)
    focus = _focus_question(brief, str(question_id).strip() if question_id else None)
    block_title = _block_title(repository, deliverable_block_id)
    if adapter is None:
        from app.adapters.http_draft import resolve_draft_adapter

        adapter = resolve_draft_adapter()
    window = snapshot[:MAX_SNAPSHOT_CHARS]
    try:
        proposals = list(
            adapter.propose(
                {
                    "task": "snapshot_excerpts",
                    "source_title": source.get("title") or "",
                    "focus_question": focus or "",
                    "block_title": block_title or "",
                    "decision_question": brief["brief"].get("decision_question") or "",
                    "snapshot_text": window,
                }
            )
            or []
        )
    except DraftSuggestionError as error:
        raise ExcerptFromSnapshotError(str(error)) from error
    except Exception as error:
        raise ExcerptFromSnapshotError(
            str(error).strip() or "模型没有从快照摘下原话。没有记下原话，也没有改稿。"
        ) from error
    excerpts = _keep_verbatim(
        [str(item.get("text") or "") for item in proposals if isinstance(item, dict)],
        snapshot,
    )
    if not excerpts:
        raise ExcerptFromSnapshotError(
            "快照里没有能原样摘下、又对上这个问题的句子。没有记下原话，也没有改稿。"
        )
    if _all_claim_flags(repository) != before_flags:
        raise ExcerptFromSnapshotError("从快照扒原话不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise ExcerptFromSnapshotError("从快照扒原话不得改写给经理的稿。")
    return {
        "source_id": source_id,
        "excerpts": excerpts,
        "workbench": build_workbench_projection(repository, project_id),
        "confirmation": {
            "recorded": True,
            "record_kind": "draft_snapshot_excerpts",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已从快照摘出原话候选。点收下才挂到这一节。核验未改，给经理的稿未改。",
        },
    }


def adopt_snapshot_excerpts(
    repository: SqliteRepository,
    source_id: str,
    *,
    deliverable_block_id: Any,
    excerpts: Any,
) -> dict[str, Any]:
    """收下快照原话并挂到这一节。不改稿，不改核验。"""
    source = repository.get_source(source_id)
    if source is None:
        raise ExcerptFromSnapshotError(f"来源 {source_id} 不存在")
    block_id = str(deliverable_block_id or "").strip()
    if not block_id:
        raise ExcerptFromSnapshotError("先点开中间那一节，再把快照原话挂上去。")
    try:
        body, content_type = read_source_snapshot(repository, source_id)
    except SnapshotError as error:
        raise ExcerptFromSnapshotError(str(error)) from error
    snapshot = snapshot_plain_text(body, content_type)
    cleaned = _keep_verbatim(
        excerpts if isinstance(excerpts, list) else [],
        snapshot,
    )
    if not cleaned:
        raise ExcerptFromSnapshotError("没有可收下的快照原话。没有记下原话，也没有改稿。")
    before_draft = _block_text(repository, block_id)
    last: dict[str, Any] | None = None
    try:
        for excerpt in cleaned:
            last = attach_claim_to_block(
                repository,
                block_id,
                source_id=source_id,
                excerpt=excerpt,
                text=excerpt,
                locator_kind="snapshot",
            )
    except ClaimAttachError as error:
        raise ExcerptFromSnapshotError(str(error)) from error
    after_draft = _block_text(repository, block_id)
    if after_draft != before_draft:
        raise ExcerptFromSnapshotError("挂快照原话不得改写给经理的稿。")
    assert last is not None
    workbench = build_workbench_projection(repository, str(source["project_id"]))
    last["workbench"] = workbench
    last["confirmation"]["record_kind"] = "adopt_snapshot_excerpts"
    last["confirmation"]["message"] = (
        f"已从快照挂上{len(cleaned)}段原话。未改给经理的稿。点中间「按材料再写一版」才会更新。"
    )
    return last


def excerpt_in_snapshot(excerpt: str, snapshot: str) -> bool:
    quote = str(excerpt or "").strip()
    if not quote or not snapshot:
        return False
    if quote in snapshot:
        return True
    return _normalize(quote) in _normalize(snapshot)


def _keep_verbatim(items: list[Any], snapshot: str) -> list[str]:
    kept: list[str] = []
    seen: set[str] = set()
    for item in items:
        quote = str(item or "").strip()
        quote = " ".join(quote.split())
        if len(quote) < MIN_EXCERPT_CHARS or len(quote) > MAX_EXCERPT_CHARS:
            continue
        if not excerpt_in_snapshot(quote, snapshot):
            continue
        key = _normalize(quote)
        if key in seen:
            continue
        seen.add(key)
        kept.append(quote)
        if len(kept) >= MAX_EXCERPTS:
            break
    return kept


def _focus_question(brief: dict[str, Any], question_id: str | None) -> str | None:
    if not question_id:
        return None
    current = int(brief.get("current_round") or 1)
    for item in brief["questions"]:
        if item["id"] != question_id:
            continue
        if int(item.get("round_index") or 1) != current:
            raise ExcerptFromSnapshotError(
                "上一轮已经收口。换本轮问题再从快照扒原话。没有记下原话，也没有改稿。"
            )
        if item["status"] == DEFERRED_STATUS:
            raise ExcerptFromSnapshotError(
                "这条本轮问题这轮先不用，换一条再从快照扒原话。没有记下原话，也没有改稿。"
            )
        return item["question"]
    raise ExcerptFromSnapshotError(
        "没有这条本轮问题。没有记下原话，也没有改稿。"
    )


def _block_title(repository: SqliteRepository, block_id: Any) -> str | None:
    key = str(block_id or "").strip()
    if not key:
        return None
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT title FROM deliverable_blocks WHERE id = ?",
            (key,),
        ).fetchone()
    if row is None:
        return None
    return str(row["title"] or "").strip() or None


def _normalize(value: str) -> str:
    return " ".join(value.split())


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
    return {str(row["id"]): str(row["current_text"] or "") for row in rows}


def _block_text(repository: SqliteRepository, block_id: str) -> str:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()
    if row is None:
        raise ExcerptFromSnapshotError(f"报告段落 {block_id} 不存在")
    return str(row["current_text"] or "")
