from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.brief import build_brief_projection
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection

class MaterialQuestionError(ValueError):
    pass


def assign_material_question(
    repository: SqliteRepository,
    *,
    source_id: Any = None,
    candidate_id: Any = None,
    question_id: Any,
) -> dict[str, Any]:
    """把已有材料标到本轮问题上。不改稿，不改核验，也不覆盖旧文件。"""
    source_key = str(source_id or "").strip() or None
    candidate_key = str(candidate_id or "").strip() or None
    question_key = str(question_id or "").strip()
    if bool(source_key) == bool(candidate_key):
        raise MaterialQuestionError("一次只能给一份来源或一份候选标问题。")
    if not question_key:
        raise MaterialQuestionError("先点开左边那条问题，再把材料归过去。")
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()
    with repository.connect() as connection:
        if source_key:
            row = connection.execute(
                "SELECT id, project_id FROM sources WHERE id = ?",
                (source_key,),
            ).fetchone()
            if row is None:
                raise MaterialQuestionError(f"来源 {source_key} 不存在")
            project_id = str(row["project_id"])
            table = "sources"
            row_id = source_key
        else:
            row = connection.execute(
                "SELECT id, project_id FROM candidate_sources WHERE id = ?",
                (candidate_key,),
            ).fetchone()
            if row is None:
                raise MaterialQuestionError(f"候选 {candidate_key} 不存在")
            project_id = str(row["project_id"])
            table = "candidate_sources"
            row_id = candidate_key
    _require_current_question(repository, project_id, question_key)
    with repository.transaction() as connection:
        connection.execute(
            f"UPDATE {table} SET research_question_id = ?, updated_at = ? WHERE id = ?",
            (question_key, now, row_id),
        )
    if _all_claim_flags(repository) != before_flags:
        raise MaterialQuestionError("给材料标问题不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise MaterialQuestionError("给材料标问题不得改写给经理的稿。")
    workbench = build_workbench_projection(repository, project_id)
    return {
        "source_id": source_key,
        "candidate_id": candidate_key,
        "research_question_id": question_key,
        "workbench": workbench,
        "confirmation": {
            "recorded": True,
            "record_kind": "assign_material_question",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": "已把这份材料标到点开的问题上。核验未改，给经理的稿未改。",
        },
    }


def assign_materials_question(
    repository: SqliteRepository,
    project_id: str,
    *,
    source_ids: Any = None,
    candidate_ids: Any = None,
    question_id: Any,
) -> dict[str, Any]:
    """把人点选的几份材料一次标到本轮问题上。

    只处理明确点名的 id：不按「全部」推断，也不替人猜旧材料该归哪条。
    不改稿，不改核验，也不覆盖旧文件。
    """
    project_key = str(project_id or "").strip()
    if not repository.has_project(project_key):
        raise MaterialQuestionError(f"项目 {project_key} 不存在")
    question_key = str(question_id or "").strip()
    if not question_key:
        raise MaterialQuestionError("先点开左边那条问题，再把材料归过去。")
    sources = _clean_ids(source_ids)
    candidates = _clean_ids(candidate_ids)
    if not sources and not candidates:
        raise MaterialQuestionError("先勾上要归过去的材料。")
    _require_current_question(repository, project_key, question_key)
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    now = datetime.now(UTC).isoformat()
    with repository.connect() as connection:
        for table, keys in (("sources", sources), ("candidate_sources", candidates)):
            for key in keys:
                row = connection.execute(
                    f"SELECT project_id FROM {table} WHERE id = ?", (key,)
                ).fetchone()
                if row is None:
                    raise MaterialQuestionError(f"材料 {key} 不存在")
                if str(row["project_id"]) != project_key:
                    raise MaterialQuestionError(f"材料 {key} 不属于这道题目")
    with repository.transaction() as connection:
        for table, keys in (("sources", sources), ("candidate_sources", candidates)):
            for key in keys:
                connection.execute(
                    f"UPDATE {table} SET research_question_id = ?, updated_at = ?"
                    " WHERE id = ?",
                    (question_key, now, key),
                )
    if _all_claim_flags(repository) != before_flags:
        raise MaterialQuestionError("给材料标问题不得改变主张核验。")
    if _all_block_texts(repository) != before_drafts:
        raise MaterialQuestionError("给材料标问题不得改写给经理的稿。")
    total = len(sources) + len(candidates)
    return {
        "source_ids": sources,
        "candidate_ids": candidates,
        "research_question_id": question_key,
        "workbench": build_workbench_projection(repository, project_key),
        "confirmation": {
            "recorded": True,
            "record_kind": "assign_materials_question",
            "current_text_unchanged": True,
            "verification_status_unchanged": True,
            "message": (
                f"已把{total}份材料标到点开的问题上。核验未改，给经理的稿未改。"
            ),
        },
    }


def _clean_ids(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise MaterialQuestionError("材料清单要是一组 id。")
    seen: list[str] = []
    for item in raw:
        key = str(item or "").strip()
        if key and key not in seen:
            seen.append(key)
    return seen


def _require_current_question(
    repository: SqliteRepository, project_id: str, question_id: str
) -> None:
    brief = build_brief_projection(repository, project_id)
    current = int(brief.get("current_round") or 1)
    for item in brief["questions"]:
        if item["id"] != question_id:
            continue
        if int(item.get("round_index") or 1) != current:
            raise MaterialQuestionError("上一轮已经收口。换本轮问题再标材料。")
        if item["status"] == DEFERRED_STATUS:
            raise MaterialQuestionError("这条本轮问题这轮先不用，换一条再标材料。")
        return
    raise MaterialQuestionError("没有这条本轮问题。")


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
