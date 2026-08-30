from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.update_brief import BriefUpdateError, update_brief
from app.projections.brief import build_brief_projection
from app.projections.workbench import (
    DEFERRED_STATUS,
    LEDGER_STATUS_FROM_PROGRESS,
    build_workbench_projection,
)


class QuestionProgressError(ValueError):
    pass


def set_question_progress(
    repository: SqliteRepository,
    question_id: str,
    progress: Any,
) -> dict[str, Any]:
    """只改本轮问题的三档进度；不改变主张核验，也不改写给经理的稿。"""
    key = str(progress or "").strip()
    status = LEDGER_STATUS_FROM_PROGRESS.get(key)
    if status is None:
        raise QuestionProgressError("进度只能是还没写、草稿或这轮够用了")
    project_id, row = _question_row(repository, question_id)
    if row["status"] == DEFERRED_STATUS:
        raise QuestionProgressError("这条这轮先不用。要点这轮再用，才能改进度。")
    try:
        result = update_brief(
            repository,
            project_id,
            questions=[{"id": question_id, "status": status}],
        )
    except BriefUpdateError as error:
        raise QuestionProgressError(str(error)) from error
    return _workbench_result(
        repository,
        project_id,
        result,
        message="进度已记下。核验未改，给经理的稿未改。",
        record_kind="question_progress",
        question_id=question_id,
    )


def add_research_question(
    repository: SqliteRepository,
    project_id: str,
    *,
    question: Any,
    enough_for_now: Any = None,
) -> dict[str, Any]:
    """补一条本轮问题；不删旧问题，不改变主张核验，也不改写给经理的稿。"""
    text = str(question or "").strip()
    if not text:
        raise QuestionProgressError("本轮问题不能空着")
    if not repository.has_project(project_id):
        raise QuestionProgressError(f"项目 {project_id} 不存在")
    before_ids = {
        item["id"] for item in build_brief_projection(repository, project_id)["questions"]
    }
    payload: dict[str, Any] = {"question": text, "status": "not_started"}
    why = str(enough_for_now or "").strip()
    if why:
        payload["enough_for_now"] = why
    try:
        result = update_brief(repository, project_id, questions=[payload])
    except BriefUpdateError as error:
        raise QuestionProgressError(str(error)) from error
    added = next(
        item
        for item in result["brief_projection"]["questions"]
        if item["id"] not in before_ids
    )
    return _workbench_result(
        repository,
        project_id,
        result,
        message="已补上这条本轮问题。核验未改，给经理的稿未改。",
        record_kind="add_question",
        question_id=added["id"],
    )


def defer_research_question(
    repository: SqliteRepository,
    question_id: str,
) -> dict[str, Any]:
    """这轮先不用这条；写入已替代，不删除对象，也不改核验或给经理的稿。"""
    project_id, row = _question_row(repository, question_id)
    if row["status"] != DEFERRED_STATUS:
        active = _active_count(repository, project_id)
        if active <= 1:
            raise QuestionProgressError("至少留一条本轮问题。这轮不用的可以点这轮再用。")
    try:
        result = update_brief(
            repository,
            project_id,
            questions=[{"id": question_id, "status": DEFERRED_STATUS}],
        )
    except BriefUpdateError as error:
        raise QuestionProgressError(str(error)) from error
    return _workbench_result(
        repository,
        project_id,
        result,
        message="这条这轮先不用。核验未改，给经理的稿未改。",
        record_kind="defer_question",
        question_id=question_id,
    )


def restore_research_question(
    repository: SqliteRepository,
    question_id: str,
) -> dict[str, Any]:
    """把这轮先不用的问题再启用；进度回到还没写，不改核验或给经理的稿。"""
    project_id, row = _question_row(repository, question_id)
    status = "not_started" if row["status"] == DEFERRED_STATUS else row["status"]
    try:
        result = update_brief(
            repository,
            project_id,
            questions=[{"id": question_id, "status": status}],
        )
    except BriefUpdateError as error:
        raise QuestionProgressError(str(error)) from error
    return _workbench_result(
        repository,
        project_id,
        result,
        message="这条又回到本轮。核验未改，给经理的稿未改。",
        record_kind="restore_question",
        question_id=question_id,
    )


def remove_research_question(
    repository: SqliteRepository,
    question_id: str,
) -> dict[str, Any]:
    """去掉一条没挂任何材料的问题。挂了材料的拒绝，并说清挂着几份。

    照搬 `remove_source` 立下的规矩（PRD 20.9 之后那条现场缺陷）：**没挂东西的
    可以真去掉，挂了东西的拒绝并说明理由。** 原来问题只能「这轮先不用」，
    于是拆错一次那条问题就永远躺在抽屉里，底下的材料也跟着赖着——
    产品所有者做「AIGC 有哪些新兴公司」时真踩到了：第一条问题拆歪，
    后面搜回来的材料全跟着歪，却没有任何清理手段。

    为什么挂了材料就不许删：材料的「归到哪条问题」是人手工点的，删掉问题会让
    那批材料悄悄退回「还没标对应问题」，人不会收到提示，下一轮又会重新捡起来。
    先把材料改归到别的问题、或者把材料本身去掉，再回来删这条。
    """
    project_id, _row = _question_row(repository, question_id)
    with repository.connect() as connection:
        blockers = []
        attached = connection.execute(
            "SELECT COUNT(*) FROM sources WHERE research_question_id = ?",
            (question_id,),
        ).fetchone()[0]
        if attached:
            blockers.append(str(attached) + " 份材料")
        candidates = connection.execute(
            "SELECT COUNT(*) FROM candidate_sources WHERE research_question_id = ?",
            (question_id,),
        ).fetchone()[0]
        if candidates:
            blockers.append(str(candidates) + " 条网页候选")
        row = connection.execute(
            "SELECT question, label FROM research_questions WHERE id = ?",
            (question_id,),
        ).fetchone()
    if blockers:
        raise QuestionProgressError(
            "这条问题上还归着 " + "、".join(blockers) + "，不能去掉。"
            "先把材料改归到别的问题，或者把材料本身去掉，再回来删这条。"
            "也可以直接点「这轮先不用」，把它收起来。"
        )
    name = str((row["label"] if row else "") or (row["question"] if row else "") or question_id)
    with repository.transaction() as connection:
        connection.execute(
            "DELETE FROM research_questions WHERE id = ?", (question_id,)
        )
    workbench = build_workbench_projection(repository, project_id)
    return {
        "question_id": question_id,
        "workbench": workbench,
        "brief_projection": build_brief_projection(repository, project_id),
        "confirmation": {
            "message": "已去掉「" + name[:20] + "」。核验未改，给经理的稿未改。",
            "record_kind": "remove_question",
            "current_text_unchanged": True,
        },
    }


def set_question_target_block(
    repository: SqliteRepository,
    question_id: str,
    target_block_id: Any,
) -> dict[str, Any]:
    """定这条问题落在稿的哪一节；传空就是「还没定」。

    先立骨架再找料：知道这条问题最后要写进哪一节，找到材料时才知道往哪儿挂。
    定不了就留空，页面上写「还没定」——不替人猜，跟材料归属那条规矩一致。
    """
    project_id, _row = _question_row(repository, question_id)
    key = str(target_block_id or "").strip()
    if key:
        with repository.connect() as connection:
            block = connection.execute(
                "SELECT id, project_id, title FROM deliverable_blocks WHERE id = ?",
                (key,),
            ).fetchone()
        if block is None:
            raise QuestionProgressError(f"稿里没有这一节（{key}）")
        if str(block["project_id"]) != project_id:
            raise QuestionProgressError("这一节不在这个题目里，不能挂过去。")
        message = "已记下这条问题落在「" + str(block["title"]) + "」。核验未改，给经理的稿未改。"
    else:
        message = "已改回还没定落在哪一节。核验未改，给经理的稿未改。"
    try:
        result = update_brief(
            repository,
            project_id,
            questions=[{"id": question_id, "target_block_id": key or None}],
        )
    except BriefUpdateError as error:
        raise QuestionProgressError(str(error)) from error
    return _workbench_result(
        repository,
        project_id,
        result,
        message=message,
        record_kind="question_target_block",
        question_id=question_id,
    )


def _workbench_result(
    repository: SqliteRepository,
    project_id: str,
    result: dict[str, Any],
    *,
    message: str,
    record_kind: str,
    question_id: str,
) -> dict[str, Any]:
    workbench = build_workbench_projection(repository, project_id)
    confirmation = dict(result["confirmation"])
    confirmation["message"] = message
    confirmation["record_kind"] = record_kind
    return {
        "question_id": question_id,
        "workbench": workbench,
        "brief_projection": result["brief_projection"],
        "confirmation": confirmation,
    }


def _question_row(
    repository: SqliteRepository, question_id: str
) -> tuple[str, dict[str, Any]]:
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT project_id, status, round_index FROM research_questions WHERE id = ?",
            (question_id,),
        ).fetchone()
        current_row = None
        if row is not None:
            current_row = connection.execute(
                "SELECT current_round FROM projects WHERE id = ?",
                (row["project_id"],),
            ).fetchone()
    if row is None:
        raise QuestionProgressError(f"本轮问题 {question_id} 不存在")
    current = int((current_row["current_round"] if current_row else 1) or 1)
    if int(row["round_index"] or 1) != current:
        raise QuestionProgressError("上一轮已经收口。本轮再拆或补一条。")
    return str(row["project_id"]), {"status": row["status"]}


def _active_count(repository: SqliteRepository, project_id: str) -> int:
    with repository.connect() as connection:
        current_row = connection.execute(
            "SELECT current_round FROM projects WHERE id = ?",
            (project_id,),
        ).fetchone()
        current = int((current_row["current_round"] if current_row else 1) or 1)
        row = connection.execute(
            """
            SELECT COUNT(*) AS n FROM research_questions
            WHERE project_id = ? AND status != ? AND round_index = ?
            """,
            (project_id, DEFERRED_STATUS, current),
        ).fetchone()
    return int(row["n"])
