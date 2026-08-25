from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.create_project import PLACEHOLDER_TEXT
from app.templates.registry import TemplateError, load_template
from app.projections.brief import build_brief_projection
from app.projections.candidates import build_candidate_source_projection
from app.projections.checks import mechanical_checks, unsourced_numbers
from app.projections.impact import cross_section_impact
from app.projections.numbers import number_manifest
from app.projections.report import build_report_projection, build_review_context
from app.projections.sources import build_source_list_projection

PROGRESS_FROM_STATUS = {
    "not_started": "unwritten",
    "enough_for_now": "enough",
}
PROGRESS_LABELS = {
    "unwritten": "还没写",
    "draft": "草稿",
    "enough": "这轮够用了",
}
LEDGER_STATUS_FROM_PROGRESS = {
    "unwritten": "not_started",
    "draft": "in_progress",
    "enough": "enough_for_now",
}
DEFERRED_STATUS = "superseded"


def progress_from_status(status: str | None) -> str:
    return PROGRESS_FROM_STATUS.get(status or "", "draft")


def _project_question(
    row: dict[str, Any],
    *,
    can_defer: bool,
    block_titles: dict[str, str] | None = None,
) -> dict[str, Any]:
    deferred = row["status"] == DEFERRED_STATUS
    round_index = int(row.get("round_index") or 1)
    # 这条问题打算落在稿的哪一节。对不上就留空，页面上写「还没定」，不替人猜。
    target_block_id = row.get("target_block_id") or None
    target_section = (block_titles or {}).get(str(target_block_id or ""))
    if target_block_id and not target_section:
        # 那一节被删掉了：位置还记着，但已经指不到东西，按没定处理。
        target_block_id = None
    return {
        "id": row["id"],
        "question": row["question"],
        "target_block_id": target_block_id,
        "target_section": target_section,
        "target_section_label": target_section or "还没定落在哪一节",
        # 第一层只显示短名，点开后再看完整问题。
        "label": row.get("label"),
        "short_label": row.get("short_label") or row["question"],
        "why_it_matters": row["enough_for_now"],
        "progress": progress_from_status(row["status"]),
        "progress_label": PROGRESS_LABELS[progress_from_status(row["status"])],
        "deferred": deferred,
        "can_defer": can_defer and not deferred,
        "round_index": round_index,
        "round_label": "第 " + str(round_index) + " 轮",
    }


def build_workbench_projection(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    """一块工作台：本轮问题、给经理的稿、材料匣。只读同一对象，不复制结论。"""
    brief = build_brief_projection(repository, project_id)
    report = build_report_projection(repository, project_id)
    sources = build_source_list_projection(repository, project_id)
    candidates = build_candidate_source_projection(repository, project_id)
    superseded = _superseding_targets(repository, project_id)
    fresh_material = _material_since_last_adopted(repository, project_id)
    sibling_count = len(report["blocks"])
    blocks = []
    for item in report["blocks"]:
        review = build_review_context(repository, item["id"])
        blocks.append(
            _project_block(
                item,
                review,
                superseded,
                sibling_count,
                fresh_material.get(str(item["id"]), 0),
            )
        )
    # 改一节，别的哪几节跟着要看一眼。占位稿不参与：里面还没有结论。
    impact = cross_section_impact(
        [item for item in blocks if not item["placeholder"]]
    )
    for item in blocks:
        item["impact"] = impact.get(
            str(item["id"]),
            {
                "related": [],
                "total": 0,
                "changed_numbers": [],
                "heading": "这一节还没写，改不到别处。",
                "limitation": "",
            },
        )
    current_round = int(brief.get("current_round") or 1)
    current_rows = [
        row
        for row in brief["questions"]
        if int(row.get("round_index") or 1) == current_round
    ]
    archived_rows = [
        row
        for row in brief["questions"]
        if int(row.get("round_index") or 1) < current_round
    ]
    active_rows = [
        row for row in current_rows if row["status"] != DEFERRED_STATUS
    ]
    deferred_rows = [
        row for row in current_rows if row["status"] == DEFERRED_STATUS
    ]
    can_defer = len(active_rows) > 1
    block_titles = {str(item["id"]): item["title"] for item in report["blocks"]}
    questions = [
        _project_question(row, can_defer=can_defer, block_titles=block_titles)
        for row in active_rows
    ]
    deferred_questions = [
        _project_question(row, can_defer=False, block_titles=block_titles)
        for row in deferred_rows
    ]
    archived_rounds = _archived_rounds(
        archived_rows, _round_sections(repository, project_id), block_titles
    )
    return {
        "project": {
            "id": report["project"]["id"],
            "name": report["project"]["name"],
            "stage": report["project"]["stage"],
            "template_key": report["project"].get("template_key"),
            "template_name": _template_name(report["project"].get("template_key")),
        },
        "decision": brief["brief"]["decision_question"],
        "brief_id": brief["brief"]["id"],
        "current_round": current_round,
        "round_label": "第 " + str(current_round) + " 轮",
        "can_close_round": bool(current_rows),
        "questions": questions,
        "deferred_questions": deferred_questions,
        "archived_rounds": archived_rounds,
        "blocks": blocks,
        "materials": {
            "sources": sources["sources"],
            "candidates": _material_box_candidates(
                candidates["candidates"], sources["sources"]
            ),
            # 「这轮不用」是收起来不是删掉：单独一个抽屉，随时能拿回来。
            "set_aside": _deduped_set_aside(
                [
                    item
                    for item in candidates["candidates"]
                    if item["status"] == "discarded"
                ]
            ),
        },
    }


def _material_box_candidates(
    candidates: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """匣子里一份材料只出现一次。

    已升为来源的候选由来源那一行代表：来源那行带着快照、原链接和已挂原话，
    候选行只会让同一份材料在匣子里重复一遍。排除的候选仍不显示。对象都不删。
    """
    promoted = {str(item["id"]) for item in sources}
    kept: list[dict[str, Any]] = []
    for item in candidates:
        if item["status"] == "discarded":
            continue
        if (
            item["status"] == "promoted"
            and str(item.get("promoted_source_id") or "") in promoted
        ):
            continue
        kept.append(item)
    return kept


def _deduped_set_aside(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一条链接被搜到又排除了不止一次，只在抽屉里出现一次。

    根因已在搜索侧堵住（`_existing_urls` 现在把排除过的链接也算「见过」），
    这里是给历史数据兜底：旧的重复候选对象仍在库里、仍不删，只是不再让人
    在「这轮不用的」里看到两条长得一样、状态各自独立的行。同一网址取最近
    一条代表，位置按它在列表里最后一次出现的地方。
    """
    latest_by_key: dict[str, dict[str, Any]] = {}
    for item in items:
        key = str(item.get("url") or "").strip() or str(item["id"])
        latest_by_key[key] = item
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for item in items:
        key = str(item.get("url") or "").strip() or str(item["id"])
        if key in seen:
            continue
        seen.add(key)
        ordered.append(latest_by_key[key])
    return ordered


def _project_block(
    item: dict[str, Any],
    review: dict[str, Any],
    superseded: set[str],
    sibling_count: int,
    fresh_material: int = 0,
) -> dict[str, Any]:
    pending = review.get("pending_revisions") or []
    latest = pending[-1] if pending else None
    claims = review.get("claims") or []
    text = item["current_text"] or ""
    claim_sources = []
    for claim in claims:
        source = claim.get("source") or {}
        delivery = str(claim.get("delivery_rule") or "")
        client = "客户提供" in delivery
        macro = "不单独证明项目需求" in delivery or "宏观" in str(
            source.get("limitation") or ""
        )
        claim_sources.append(
            {
                "claim_id": claim["id"],
                "claim_text": claim.get("text"),
                "source_id": source.get("id"),
                "source_title": source.get("title"),
                "excerpt": (claim.get("evidence") or [{}])[0].get("excerpt")
                if claim.get("evidence")
                else None,
                "client_provided": client,
                "macro": macro,
            }
        )
    checks = mechanical_checks(text, claims, superseded)
    preview_checks = None
    if latest and latest.get("body") is not None:
        preview_checks = mechanical_checks(
            str(latest.get("body") or ""), claims, superseded
        )
    return {
        "id": item["id"],
        "title": item["title"],
        "current_text": text,
        "placeholder": text.strip() == PLACEHOLDER_TEXT,
        "pending_revision": latest,
        "prior_revision": review.get("prior_revision"),
        "can_remove": sibling_count > 1
        and not claims
        and not (review.get("findings") or [])
        and not (review.get("options") or []),
        "checks": checks,
        "preview_checks": preview_checks,
        "claim_sources": claim_sources,
        # 稿里每个数字逐个对一遍出处。占位稿不用对：里面没有数字。
        "number_manifest": (
            {"numbers": [], "total": 0, "unsourced": 0, "limitation": ""}
            if text.strip() == PLACEHOLDER_TEXT
            else number_manifest(text, claim_sources)
        ),
        # 这一节上次收下之后又挂了几条原话。占位稿不算：那一节本来就还没写。
        "material_since_draft": 0 if text.strip() == PLACEHOLDER_TEXT else fresh_material,
    }


def _material_since_last_adopted(
    repository: SqliteRepository, project_id: str
) -> dict[str, int]:
    """每一节自从上次收下正文之后，又挂上了几条原话。

    补料后，受影响的已收段落要标为过时。旧的 `stale` 只在来源被
    新文件替换或人手动标过时才亮，新挂原话不算，于是人挂完材料看不出稿该动。
    这里只数，不改稿、不改核验。
    """
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT b.id AS block_id, COUNT(c.id) AS fresh
            FROM deliverable_blocks b
            JOIN deliverable_block_claims bc
              ON bc.deliverable_block_id = b.id
            JOIN claims c ON c.id = bc.claim_id
            LEFT JOIN (
                SELECT deliverable_block_id, MAX(created_at) AS adopted_at
                FROM deliverable_block_revisions
                WHERE adopted = 1
                GROUP BY deliverable_block_id
            ) r ON r.deliverable_block_id = b.id
            WHERE b.project_id = ?
              AND c.created_at > COALESCE(r.adopted_at, b.created_at)
            GROUP BY b.id
            """,
            (project_id,),
        ).fetchall()
    return {str(row["block_id"]): int(row["fresh"] or 0) for row in rows}


def _unsourced_numbers(text: str, claim_texts: list[str]) -> list[dict[str, Any]]:
    return unsourced_numbers(text, claim_texts)


def _superseding_targets(
    repository: SqliteRepository, project_id: str
) -> set[str]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT supersedes_source_id FROM sources
            WHERE project_id = ? AND supersedes_source_id IS NOT NULL
            """,
            (project_id,),
        ).fetchall()
    return {str(row["supersedes_source_id"]) for row in rows}


def _round_sections(
    repository: SqliteRepository, project_id: str
) -> dict[int, list[dict[str, Any]]]:
    """每一轮收下的是哪一版稿。

    段落对象只有一套，轮次记在版本上。这里按轮取那一轮最后一次
    收下的正文，供回看用；只读投影，不复制第二套结论。
    """
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT r.deliverable_block_id AS block_id, b.title AS title,
                   r.round_index AS round_index, r.version AS version, r.body AS body
            FROM deliverable_block_revisions r
            JOIN deliverable_blocks b ON b.id = r.deliverable_block_id
            WHERE b.project_id = ? AND r.adopted = 1
            ORDER BY r.round_index, b.rowid, r.version
            """,
            (project_id,),
        ).fetchall()
    latest: dict[tuple[int, str], dict[str, Any]] = {}
    order: dict[int, list[str]] = {}
    for row in rows:
        index = int(row["round_index"] or 1)
        block_id = str(row["block_id"])
        body = str(row["body"] or "").strip()
        if not body or body == PLACEHOLDER_TEXT:
            continue
        key = (index, block_id)
        if key not in latest:
            order.setdefault(index, []).append(block_id)
        latest[key] = {
            "id": block_id,
            "title": row["title"],
            "version": int(row["version"]),
            "text": body,
        }
    return {
        index: [latest[(index, block_id)] for block_id in block_ids]
        for index, block_ids in order.items()
    }


def _archived_rounds(
    rows: list[dict[str, Any]],
    sections: dict[int, list[dict[str, Any]]] | None = None,
    block_titles: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        index = int(row.get("round_index") or 1)
        grouped.setdefault(index, []).append(row)
    archived = []
    for index in sorted(grouped):
        archived.append(
            {
                "round_index": index,
                "round_label": "第 " + str(index) + " 轮",
                "questions": [
                    _project_question(
                        row, can_defer=False, block_titles=block_titles
                    )
                    for row in grouped[index]
                ],
                "sections": (sections or {}).get(index, []),
            }
        )
    return archived


def _template_name(key: object) -> str:
    """模板的人话名字；模板文件被挪走时退回 key，不让顶栏空着。"""
    text = str(key or "").strip()
    if not text:
        return ""
    try:
        return load_template(text).name
    except TemplateError:
        return text
