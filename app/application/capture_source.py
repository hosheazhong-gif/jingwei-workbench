from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app import SCHEMA_VERSION
from app.adapters.local_source import (
    MAX_UPLOAD_BYTES,
    CaptureError,
    DeferredParser,
    LocalFileSourceAdapter,
)
from app.adapters.sqlite_repository import SqliteRepository
from app.application.research_round import ResearchRoundError, require_current_question


def capture_local_source(
    repository: SqliteRepository,
    project_id: str,
    source_path: Path | str | None = None,
    *,
    title: str | None = None,
    supersedes_source_id: str | None = None,
    kind: str = "user_provided",
    adapter: LocalFileSourceAdapter | None = None,
    parser: DeferredParser | None = None,
    uploaded_name: str | None = None,
    uploaded_bytes: bytes | None = None,
    question_id: Any = None,
) -> dict[str, Any]:
    """保存本机文件或上传字节为新 Source。不覆盖旧来源，也不解析摘录。"""
    if not repository.has_project(project_id):
        raise CaptureError(f"项目 {project_id} 不存在")
    has_path = source_path is not None and str(source_path).strip() != ""
    has_upload = uploaded_bytes is not None
    if has_path == has_upload:
        raise CaptureError("须提供本机路径或上传文件，不能同时使用")
    if has_upload and len(uploaded_bytes) > MAX_UPLOAD_BYTES:
        raise CaptureError("文件过大，请不超过 20MB")
    if supersedes_source_id:
        previous = repository.get_source(supersedes_source_id)
        if previous is None or previous["project_id"] != project_id:
            raise CaptureError(f"被替代来源 {supersedes_source_id} 不存在于当前项目")
    try:
        linked_question = require_current_question(
            repository, project_id, question_id
        )
    except ResearchRoundError as error:
        raise CaptureError(str(error)) from error
    if linked_question is None and supersedes_source_id:
        previous = repository.get_source(supersedes_source_id)
        if previous is not None:
            linked_question = previous.get("research_question_id")

    source_id = repository.allocate_source_id(project_id)
    destination_dir = repository.files_root / project_id / "sources" / source_id
    adapter = adapter or LocalFileSourceAdapter()
    parser = parser or DeferredParser()
    try:
        if has_upload:
            name = uploaded_name if uploaded_name is not None else "upload.bin"
            captured = dict(adapter.capture_bytes(name, uploaded_bytes, destination_dir))
        else:
            captured = dict(adapter.capture(Path(source_path), destination_dir))
        snapshot_file = Path(captured["snapshot_path"])
        relative_snapshot = snapshot_file.resolve().relative_to(
            repository.files_root.resolve()
        ).as_posix()
        now = datetime.now(UTC).isoformat()
        record = {
            "id": source_id,
            "project_id": project_id,
            "kind": kind,
            "title": title or captured["title"],
            "file_name": captured["file_name"],
            "availability": "available",
            "snapshot_path": relative_snapshot,
            "content_hash": captured["content_hash"],
            "supersedes_source_id": supersedes_source_id,
            "limitation": "已保存项目内受控副本与哈希；摘录尚未解析",
            "analysis_role": None,
            "delivery_use": None,
            "schema_version": SCHEMA_VERSION,
            "created_at": now,
            "updated_at": now,
            "institution": None,
            "published_at": None,
            "original_url": None,
            "original_path": captured["original_path"],
            "permission": None,
            "sensitivity": None,
            "source_quality": None,
            "research_question_id": linked_question,
        }
        excerpt_candidates = list(parser.parse(captured))
        repository.insert_source(record)
    except CaptureError:
        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        raise
    except Exception as error:
        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        raise CaptureError("保存材料失败，未覆盖已有来源。") from error

    return {
        "source": record,
        "controlled_copy": str(snapshot_file),
        "excerpt_candidates": excerpt_candidates,
    }


MANAGER_FEEDBACK_KIND = "manager_feedback"


def capture_manager_feedback(
    repository: SqliteRepository,
    project_id: str,
    *,
    text: Any,
    title: Any = None,
) -> dict[str, Any]:
    """把经理这一轮的反馈原话收进材料匣，存成本轮的一份核心材料。

    反馈是内部指示，不是客户口径，也不是外部证据。这里只把原话存成受控副本
    （所以「看快照 / 从快照扒原话」照常可用）；归属留到挂主张时标 manager_feedback。
    不改稿，不改核验。
    """
    body = str(text or "").strip()
    if not body:
        raise CaptureError("经理反馈不能空着")
    if not repository.has_project(project_id):
        raise CaptureError(f"项目 {project_id} 不存在")
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    current_round = int((row["current_round"] if row else 1) or 1)
    name = str(title or "").strip() or ("第 " + str(current_round) + " 轮 经理反馈")
    return capture_local_source(
        repository,
        project_id,
        title=name,
        kind=MANAGER_FEEDBACK_KIND,
        uploaded_name=name + ".txt",
        uploaded_bytes=body.encode("utf-8"),
    )
