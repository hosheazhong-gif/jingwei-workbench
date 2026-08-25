"""去掉一份还没被引用的材料。

照「空节可去掉」的先例（`add_block.remove_deliverable_block`）：**没挂东西的可以
真去掉，挂了东西的一律不给删。** 升错了、传错文件了要能收拾干净；但一旦有原话、
主张或判断挂在上面，删它就等于把追溯链剪断，那是账本不允许的。

从网页候选升上来的来源被去掉时，那条候选退回「这轮先不用」——不是当作没见过。
否则再次搜索同一网址时会把它误当成新候选收回来。
"""

from __future__ import annotations

import shutil
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.source_snapshot import resolve_source_snapshot_path
from app.domain.models import CandidateSourceStatus
from app.projections.workbench import build_workbench_projection


class SourceRemoveError(ValueError):
    pass


def remove_source(
    repository: SqliteRepository, source_id: str
) -> dict[str, Any]:
    """去掉一份没有被引用的材料，连同它的受控副本。不改稿，不改核验。"""
    with repository.connect() as connection:
        source = connection.execute(
            "SELECT id, project_id, title FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise SourceRemoveError(f"材料 {source_id} 不存在")
        project_id = str(source["project_id"])
        blockers = _blockers(connection, source_id)

    if blockers:
        raise SourceRemoveError(
            "这份材料上还挂着" + "、".join(blockers) + "，不能去掉。"
            "删它等于把追溯链剪断。先在稿里收回引用它的那一版，或者把它留着不用。"
        )

    before_status = _claim_statuses(repository)
    before_drafts = _block_texts(repository)
    snapshot_path = None
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        snapshot_path = resolve_source_snapshot_path(repository, dict(row))

    with repository.transaction() as connection:
        # 升上来的那条候选退回「这轮先不用」，不是抹掉：它仍然算见过。
        connection.execute(
            """
            UPDATE candidate_sources
            SET status = ?, promoted_source_id = NULL, updated_at = datetime('now')
            WHERE promoted_source_id = ?
            """,
            (str(CandidateSourceStatus.DISCARDED), source_id),
        )
        connection.execute(
            "DELETE FROM source_qa_requirements WHERE source_id = ?", (source_id,)
        )
        connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))

    if snapshot_path is not None and snapshot_path.exists():
        if snapshot_path.is_dir():
            shutil.rmtree(snapshot_path)
        else:
            snapshot_path.unlink()

    after_status = _claim_statuses(repository)
    after_drafts = _block_texts(repository)
    if before_status != after_status:
        raise SourceRemoveError("去掉材料不得改变主张核验状态")
    if before_drafts != after_drafts:
        raise SourceRemoveError("去掉材料不得改写给经理的稿")

    return {
        "removed": True,
        "source_id": source_id,
        "workbench": build_workbench_projection(repository, project_id),
        "confirmation": {
            "recorded": True,
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已去掉这份材料和它的受控副本。稿和主张核验状态都没有改。"
            "它如果是搜来的，那条网址回到「这轮先不用」，再搜不会重复收进来。",
        },
    }


def _blockers(connection: Any, source_id: str) -> list[str]:
    found: list[str] = []
    counts = {
        "原话": "SELECT COUNT(*) FROM evidence_excerpts WHERE source_id = ?",
        "主张": "SELECT COUNT(*) FROM claims WHERE source_id = ?",
        "判断": "SELECT COUNT(*) FROM finding_sources WHERE source_id = ?",
    }
    for label, sql in counts.items():
        if connection.execute(sql, (source_id,)).fetchone()[0]:
            found.append(label)
    replaced = connection.execute(
        "SELECT COUNT(*) FROM sources WHERE supersedes_source_id = ?",
        (source_id,),
    ).fetchone()[0]
    if replaced:
        found.append("更新它的新版本")
    return found


def _claim_statuses(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        return {
            row["id"]: row["verification_status"]
            for row in connection.execute(
                "SELECT id, verification_status FROM claims"
            )
        }


def _block_texts(repository: SqliteRepository) -> dict[str, str]:
    with repository.connect() as connection:
        return {
            row["id"]: row["current_text"]
            for row in connection.execute(
                "SELECT id, current_text FROM deliverable_blocks"
            )
        }
