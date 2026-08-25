from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app import SCHEMA_VERSION
from app.adapters.local_source import CaptureError
from app.adapters.sqlite_repository import SqliteRepository
from app.adapters.web_source import WebPageSourceAdapter
from app.application.ids import allocate_prefixed_id
from app.application.research_round import ResearchRoundError, require_current_question
from app.domain import CandidateSourceStatus
from app.projections.candidates import build_candidate_source_projection
from app.projections.report import build_report_projection
from app.projections.sources import build_source_list_projection


class CandidateSourceError(ValueError):
    pass


def capture_web_candidate(
    repository: SqliteRepository,
    project_id: str,
    *,
    url: Any,
    title: Any = None,
    note: Any = None,
    question_id: Any = None,
) -> dict[str, Any]:
    """收录网页候选。未打开前不是可引用 Source，也不能写成主张。"""
    if not repository.has_project(project_id):
        raise CandidateSourceError(f"项目 {project_id} 不存在")
    try:
        linked_question = require_current_question(
            repository, project_id, question_id
        )
    except ResearchRoundError as error:
        raise CandidateSourceError(str(error)) from error
    cleaned_url = _require_http_url(url)
    cleaned_title = _optional_text(title) or _title_from_url(cleaned_url)
    cleaned_note = _optional_text(note)
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()

    with repository.transaction() as connection:
        duplicate = connection.execute(
            """
            SELECT id FROM candidate_sources
            WHERE project_id = ? AND url = ? AND status IN ('captured', 'opened')
            """,
            (project_id, cleaned_url),
        ).fetchone()
        if duplicate is not None:
            raise CandidateSourceError("该链接已在候选列表中，须先打开或升为来源")
        candidate_id = allocate_prefixed_id(connection, "candidate_sources", "CS")
        connection.execute(
            """
            INSERT INTO candidate_sources (
                id, project_id, url, title, note, status, opened_at,
                promoted_source_id, schema_version, created_at, updated_at,
                research_question_id
            ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                project_id,
                cleaned_url,
                cleaned_title,
                cleaned_note,
                CandidateSourceStatus.CAPTURED.value,
                SCHEMA_VERSION,
                now,
                now,
                linked_question,
            ),
        )

    _assert_claims_and_drafts_unchanged(repository, before_flags, before_drafts)
    listing = build_candidate_source_projection(repository, project_id)
    candidate = next(item for item in listing["candidates"] if item["id"] == candidate_id)
    return {
        "candidate_id": candidate_id,
        "candidate": candidate,
        "candidates": listing,
        "confirmation": {
            "recorded": True,
            "record_kind": "capture_candidate",
            "candidate_id": candidate_id,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": (
                "已收录网页候选。尚未打开，不能当作可引用来源，也不能写成主张。"
            ),
        },
    }


def open_web_candidate(
    repository: SqliteRepository,
    candidate_id: str,
) -> dict[str, Any]:
    """记录人工打开链接。不抓取正文，不升为 Source，不改核验。"""
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()

    with repository.transaction() as connection:
        row = _require_candidate(connection, candidate_id)
        if row["status"] == CandidateSourceStatus.DISCARDED.value:
            raise CandidateSourceError("已排除的候选不能打开升为来源")
        if row["status"] == CandidateSourceStatus.CAPTURED.value:
            connection.execute(
                """
                UPDATE candidate_sources
                SET status = ?, opened_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (CandidateSourceStatus.OPENED.value, now, now, candidate_id),
            )
        opened_at = row["opened_at"] if row["status"] != CandidateSourceStatus.CAPTURED.value else now

    _assert_claims_and_drafts_unchanged(repository, before_flags, before_drafts)
    listing = build_candidate_source_projection(repository, row["project_id"])
    candidate = next(item for item in listing["candidates"] if item["id"] == candidate_id)
    return {
        "candidate_id": candidate_id,
        "url": row["url"],
        "opened_at": opened_at,
        "candidate": candidate,
        "candidates": listing,
        "confirmation": {
            "recorded": True,
            "record_kind": "open_candidate",
            "candidate_id": candidate_id,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "已记录打开链接。确认页面后，才能升为可引用来源。",
        },
    }


def promote_web_candidate(
    repository: SqliteRepository,
    candidate_id: str,
    *,
    title: Any = None,
    adapter: WebPageSourceAdapter | None = None,
) -> dict[str, Any]:
    """人工打开后升为 Source。不解析摘录，不改内部稿或主张核验。"""
    adapter = adapter or WebPageSourceAdapter()
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()

    with repository.connect() as connection:
        row = _require_candidate(connection, candidate_id)
    if row["status"] == CandidateSourceStatus.CAPTURED.value:
        raise CandidateSourceError("须先打开链接，未打开的候选不能升为来源")
    if row["status"] == CandidateSourceStatus.DISCARDED.value:
        raise CandidateSourceError("已排除的候选不能升为来源")
    if row["status"] == CandidateSourceStatus.PROMOTED.value:
        raise CandidateSourceError("该候选已经升为来源")

    source_id = repository.allocate_source_id()
    destination_dir = repository.files_root / row["project_id"] / "sources" / source_id
    snapshot = _try_snapshot(adapter, row["url"], destination_dir, repository)
    cleaned_title = _optional_text(title) or row["title"]
    availability = snapshot["availability"]
    relative_snapshot = snapshot.get("relative_snapshot")
    content_hash = snapshot.get("content_hash")
    limitation = snapshot["limitation"]

    with repository.transaction() as connection:
        current = _require_candidate(connection, candidate_id)
        if current["status"] != CandidateSourceStatus.OPENED.value:
            raise CandidateSourceError("须先打开链接，未打开的候选不能升为来源")
        connection.execute(
            """
            INSERT INTO sources (
                id, project_id, kind, title, file_name, availability,
                snapshot_path, content_hash, supersedes_source_id, limitation,
                analysis_role, delivery_use, schema_version, created_at, updated_at,
                institution, published_at, original_url, original_path,
                permission, sensitivity, source_quality, research_question_id
            ) VALUES (
                ?, ?, 'web_page', ?, ?, ?,
                ?, ?, NULL, ?,
                NULL, NULL, ?, ?, ?,
                NULL, NULL, ?, NULL,
                NULL, NULL, NULL, ?
            )
            """,
            (
                source_id,
                row["project_id"],
                cleaned_title,
                snapshot.get("file_name"),
                availability,
                relative_snapshot,
                content_hash,
                limitation,
                SCHEMA_VERSION,
                now,
                now,
                row["url"],
                row.get("research_question_id"),
            ),
        )
        connection.execute(
            """
            UPDATE candidate_sources
            SET status = ?, promoted_source_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (CandidateSourceStatus.PROMOTED.value, source_id, now, candidate_id),
        )

    _assert_claims_and_drafts_unchanged(repository, before_flags, before_drafts)
    listing = build_candidate_source_projection(repository, row["project_id"])
    candidate = next(item for item in listing["candidates"] if item["id"] == candidate_id)
    source = repository.get_source(source_id)
    report = build_report_projection(repository, row["project_id"])
    return {
        "candidate_id": candidate_id,
        "source_id": source_id,
        "source": dict(source) if source is not None else None,
        "candidate": candidate,
        "candidates": listing,
        "sources": build_source_list_projection(repository, row["project_id"]),
        "report": report,
        "confirmation": {
            "recorded": True,
            "record_kind": "promote_candidate",
            "candidate_id": candidate_id,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": (
                "已升为可引用来源。未自动抽取摘录，也未改变主张核验或内部稿。"
            ),
        },
    }


def discard_web_candidate(
    repository: SqliteRepository, candidate_id: str
) -> dict[str, Any]:
    """这轮不用该网页候选。不删已升来源，不改内部稿或核验。"""
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        row = _require_candidate(connection, candidate_id)
        if row["status"] == CandidateSourceStatus.PROMOTED.value:
            raise CandidateSourceError("已升为来源的链接不能从材料匣删掉")
        if row["status"] == CandidateSourceStatus.DISCARDED.value:
            raise CandidateSourceError("该候选已经排除")
        connection.execute(
            """
            UPDATE candidate_sources
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (CandidateSourceStatus.DISCARDED.value, now, candidate_id),
        )
    _assert_claims_and_drafts_unchanged(repository, before_flags, before_drafts)
    listing = build_candidate_source_projection(repository, row["project_id"])
    return {
        "candidate_id": candidate_id,
        "candidates": listing,
        "confirmation": {
            "recorded": True,
            "record_kind": "discard_candidate",
            "candidate_id": candidate_id,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "这轮不用这条链接。未改来源、内部稿和核验。",
        },
    }


def restore_web_candidate(
    repository: SqliteRepository, candidate_id: str
) -> dict[str, Any]:
    """把这轮不用的链接再拿回来。

    「这轮不用」是收起来，不是删掉：候选对象一直在，只是不摆在台面上。
    打开过的仍算打开过，不退回未打开，也不改来源、内部稿和核验。
    """
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        row = _require_candidate(connection, candidate_id)
        if row["status"] != CandidateSourceStatus.DISCARDED.value:
            raise CandidateSourceError("这条本来就在材料匣里")
        back = (
            CandidateSourceStatus.OPENED.value
            if row["opened_at"]
            else CandidateSourceStatus.CAPTURED.value
        )
        connection.execute(
            """
            UPDATE candidate_sources
            SET status = ?, updated_at = ?
            WHERE id = ?
            """,
            (back, now, candidate_id),
        )
    _assert_claims_and_drafts_unchanged(repository, before_flags, before_drafts)
    listing = build_candidate_source_projection(repository, row["project_id"])
    return {
        "candidate_id": candidate_id,
        "candidates": listing,
        "confirmation": {
            "recorded": True,
            "record_kind": "restore_candidate",
            "candidate_id": candidate_id,
            "verification_status_unchanged": True,
            "current_text_unchanged": True,
            "message": "这条又拿回材料匣了。未改来源、内部稿和核验。",
        },
    }


def _try_snapshot(
    adapter: WebPageSourceAdapter,
    url: str,
    destination_dir: Any,
    repository: SqliteRepository,
) -> dict[str, Any]:
    try:
        captured = dict(adapter.snapshot(url, destination_dir))
        snapshot_file = captured["snapshot_path"]
        relative = snapshot_file.resolve().relative_to(
            repository.files_root.resolve()
        ).as_posix()
        return {
            "file_name": captured.get("file_name"),
            "relative_snapshot": relative,
            "content_hash": captured.get("content_hash"),
            "availability": captured.get("availability") or "available",
            "limitation": "已人工打开链接并保存快照；摘录尚未解析，不能当作已核实结论。",
        }
    except CaptureError:
        return {
            "file_name": None,
            "relative_snapshot": None,
            "content_hash": None,
            "availability": "path_expired",
            "limitation": "已人工打开链接，但未能保存快照；摘录尚未解析，不能当作已核实结论。",
        }


def _require_candidate(connection: Any, candidate_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM candidate_sources WHERE id = ?",
        (candidate_id,),
    ).fetchone()
    if row is None:
        raise CandidateSourceError(f"网页候选 {candidate_id} 不存在")
    return dict(row)


def _require_http_url(value: Any) -> str:
    text = _required_text(value, "链接")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise CandidateSourceError("链接必须是 http 或 https 地址")
    return text


def _title_from_url(url: str) -> str:
    host = urlparse(url).netloc
    return host or url


def _required_text(value: Any, label: str) -> str:
    if value is None:
        raise CandidateSourceError(f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise CandidateSourceError(f"{label}不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _all_claim_flags(repository: SqliteRepository) -> dict[str, tuple[str, object]]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, verification_status, independently_verified
            FROM claims ORDER BY rowid
            """
        )
        return {
            row["id"]: (row["verification_status"], row["independently_verified"])
            for row in rows
        }


def _all_block_texts(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        rows = connection.execute(
            "SELECT id, current_text FROM deliverable_blocks ORDER BY rowid"
        )
        return {row["id"]: row["current_text"] for row in rows}


def _assert_claims_and_drafts_unchanged(
    repository: SqliteRepository,
    before_flags: dict[str, tuple[str, object]],
    before_drafts: dict[str, str],
) -> None:
    if _all_claim_flags(repository) != before_flags:
        raise CandidateSourceError("网页候选不得改变主张核验状态")
    if _all_block_texts(repository) != before_drafts:
        raise CandidateSourceError("网页候选不得改写内部稿")
