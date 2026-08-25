from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection


class ResearchRoundError(ValueError):
    pass


def project_current_round(repository: SqliteRepository, project_id: str) -> int:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        raise ResearchRoundError(f"项目 {project_id} 不存在")
    return int(row["current_round"] or 1)


def close_research_round(
    repository: SqliteRepository,
    project_id: str,
) -> dict[str, Any]:
    """把本轮问题留在原 round_index 里归档，开下一轮。不删对象，不改稿，不改核验。"""
    if not repository.has_project(project_id):
        raise ResearchRoundError(f"项目 {project_id} 不存在")
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        if row is None:
            raise ResearchRoundError(f"项目 {project_id} 不存在")
        current = int(row["current_round"] or 1)
        count = connection.execute(
            """
            SELECT COUNT(*) AS n FROM research_questions
            WHERE project_id = ? AND round_index = ?
            """,
            (project_id, current),
        ).fetchone()
        if int(count["n"]) < 1:
            raise ResearchRoundError("本轮还没有问题可收口。先拆或补一条，再开下一轮。")
        connection.execute(
            """
            UPDATE projects
            SET current_round = ?, updated_at = ?
            WHERE id = ?
            """,
            (current + 1, now, project_id),
        )
    if _all_claim_flags(repository) != before_flags:
        raise ResearchRoundError("收口本轮不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise ResearchRoundError("收口本轮不得改写给经理的稿。")
    workbench = build_workbench_projection(repository, project_id)
    return {
        "project_id": project_id,
        "closed_round": current,
        "current_round": current + 1,
        "workbench": workbench,
        "confirmation": {
            "recorded": True,
            "record_kind": "close_round",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": (
                "第 "
                + str(current)
                + " 轮已收口。原先的问题和材料还在。下一轮先拆新问题，不要重复上一轮。"
            ),
        },
    }


def reopen_research_round(
    repository: SqliteRepository,
    project_id: str,
) -> dict[str, Any]:
    """这一轮还没开始，就退回上一轮继续。

    收口只是把 `current_round` 往前推一格，问题、材料和稿都还在原处，所以
    退回来是安全的——但只在这一轮确实什么都没干时才允许：一旦拆了问题、
    标了材料或收下了新版本，退回就会让那些东西掉进上一轮，属于改写历史。
    不删对象，不改稿，不改核验。
    """
    if not repository.has_project(project_id):
        raise ResearchRoundError(f"项目 {project_id} 不存在")
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()
    with repository.transaction() as connection:
        row = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        current = int((row["current_round"] if row else 1) or 1)
        if current < 2:
            raise ResearchRoundError("现在就是第 1 轮，没有上一轮可退。")
        blockers: list[str] = []
        questions = connection.execute(
            """
            SELECT COUNT(*) AS n FROM research_questions
            WHERE project_id = ? AND round_index = ?
            """,
            (project_id, current),
        ).fetchone()
        if int(questions["n"]):
            blockers.append("已经拆了本轮问题")
        revisions = connection.execute(
            """
            SELECT COUNT(*) AS n FROM deliverable_block_revisions r
            JOIN deliverable_blocks b ON b.id = r.deliverable_block_id
            WHERE b.project_id = ? AND r.round_index = ?
            """,
            (project_id, current),
        ).fetchone()
        if int(revisions["n"]):
            blockers.append("这一轮已经写过稿")
        if blockers:
            raise ResearchRoundError(
                "这一轮已经开始了（"
                + "；".join(blockers)
                + "），不能退回上一轮。退回会把这些掉进上一轮，等于改写历史。"
            )
        connection.execute(
            "UPDATE projects SET current_round = ?, updated_at = ? WHERE id = ?",
            (current - 1, now, project_id),
        )
    if _all_claim_flags(repository) != before_flags:
        raise ResearchRoundError("退回上一轮不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise ResearchRoundError("退回上一轮不得改写给经理的稿。")
    return {
        "project_id": project_id,
        "current_round": current - 1,
        "workbench": build_workbench_projection(repository, project_id),
        "confirmation": {
            "recorded": True,
            "record_kind": "reopen_round",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": (
                "已退回第 " + str(current - 1) + " 轮继续。问题、材料和稿都还是原来那些。"
            ),
        },
    }


def require_current_question(
    repository: SqliteRepository,
    project_id: str,
    question_id: Any,
    *,
    empty_ok: bool = True,
) -> str | None:
    """校验问题属于本项目本轮且仍在用。空值在 empty_ok 时放过。"""
    key = str(question_id or "").strip()
    if not key:
        if empty_ok:
            return None
        raise ResearchRoundError("先点开左边要回答的那条。")
    with repository.connect() as connection:
        row = connection.execute(
            """
            SELECT project_id, status, round_index FROM research_questions
            WHERE id = ?
            """,
            (key,),
        ).fetchone()
        current_row = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
    if row is None:
        raise ResearchRoundError("没有这条本轮问题。")
    if str(row["project_id"]) != project_id:
        raise ResearchRoundError("这条本轮问题不在这题里。")
    if current_row is None:
        raise ResearchRoundError(f"项目 {project_id} 不存在")
    current = int(current_row["current_round"] or 1)
    if int(row["round_index"] or 1) != current:
        raise ResearchRoundError("上一轮已经收口。换本轮问题再搜或挂材料。")
    if row["status"] == DEFERRED_STATUS:
        raise ResearchRoundError("这条这轮先不用。要点这轮再用，才能带着它搜或写。")
    return key


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
