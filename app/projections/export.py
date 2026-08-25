from __future__ import annotations

import json
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.checks import mechanical_checks, unsourced_marks
from app.projections.report import build_report_projection, build_review_context

_REVIEW_LABELS = {
    "approve": "已批准进入本版",
    "modify": "已退回修改，导出的是当前内部稿",
    "exclude": "从本版排除",
}
_OVERRIDE_LABELS = {
    "assumption": "按假设推进",
    "exclude": "从本版排除",
    "scenario": "按情景表达",
}


def build_approved_export_projection(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    """本版纳入的段落投影：当前内部稿，不含未采用候选，也不含已排除段落。"""
    report = build_report_projection(repository, project_id)
    superseded = _superseded_source_ids(repository, project_id)
    rounds = _block_rounds(repository, project_id)
    included: list[tuple[dict[str, Any], dict[str, Any]]] = []
    omitted_titles: list[str] = []
    project_override = None
    for block in report["blocks"]:
        context = build_review_context(repository, block["id"])
        if project_override is None:
            project_override = context.get("project_override")
        if _excluded_from_version(context):
            omitted_titles.append(block["title"])
        else:
            included.append((block, context))

    approved = [
        _approved_block(
            report["project"],
            block,
            context,
            omitted_titles,
            superseded=superseded,
            round_index=rounds.get(str(block["id"])),
        )
        for block, context in included
    ]
    if not approved:
        approved.append(
            _header_fields(report["project"], project_override, omitted_titles)
        )
    return {
        "project": dict(report["project"]),
        "filename": _filename(report["project"]["name"]),
        "project_override": project_override,
        "omitted_titles": omitted_titles,
        "approved_blocks": approved,
    }


def _excluded_from_version(context: dict[str, Any]) -> bool:
    review = context.get("latest_review") or {}
    override = context.get("latest_override") or {}
    return review.get("action") == "exclude" or override.get("handling") == "exclude"


def _approved_block(
    project: dict[str, Any],
    block: dict[str, Any],
    context: dict[str, Any],
    omitted_titles: list[str],
    *,
    superseded: set[str] | None = None,
    round_index: int | None = None,
) -> dict[str, Any]:
    item = _header_fields(project, context.get("project_override"), omitted_titles)
    review = context.get("latest_review")
    override = context.get("latest_override")
    item.update(
        {
            "id": block["id"],
            "title": block["title"],
            "current_text": block["current_text"],
            "restriction": block.get("restriction"),
            "current_version": block["current_version"],
            "review_action": None if review is None else review["action"],
            "review_label": None
            if review is None
            else _REVIEW_LABELS.get(review["action"], review["action"]),
            "override_handling": None if override is None else override["handling"],
            "override_label": None
            if override is None
            else _OVERRIDE_LABELS.get(override["handling"], override["handling"]),
            "claims": [
                {
                    "text": claim["text"],
                    "delivery_rule": claim.get("delivery_rule"),
                    "provenance_scope": claim.get("provenance_scope"),
                    "independently_verified": claim.get("independently_verified"),
                    "verification_status": claim.get("verification_status"),
                    "source_title": (claim.get("source") or {}).get("title"),
                    "source_id": (claim.get("source") or {}).get("id"),
                    "source_url": (claim.get("source") or {}).get("original_url"),
                    "source_file": (claim.get("source") or {}).get("file_name"),
                    "source_limitation": (claim.get("source") or {}).get("limitation"),
                    # 详细版要能一路看到原话；经理版不读这个字段。
                    "evidence": [
                        {
                            "text": str(piece.get("excerpt") or "").strip(),
                            "locator": _locator_label(piece.get("locator")),
                            "context_limit": piece.get("context_limit"),
                        }
                        for piece in (claim.get("evidence") or [])
                        if str(piece.get("excerpt") or "").strip()
                    ],
                }
                for claim in context.get("claims") or []
            ],
            # 导出里「来源」只要名称加链接或文件名，正文才是主体。
            "sources": _source_lines(context.get("claims") or []),
            "unsourced_marks": unsourced_marks(
                block["current_text"] or "",
                [str(claim.get("text") or "") for claim in context.get("claims") or []],
            ),
            # 详细版要把机械检查一起交出去：读稿的人得知道哪几句还没有出处。
            "checks": mechanical_checks(
                block["current_text"] or "",
                list(context.get("claims") or []),
                set(superseded or ()),
            ),
            "round_index": round_index,
            "round_label": None if round_index is None else f"第 {round_index} 轮",
            "findings": [
                {
                    "text": finding.get("text"),
                    "confidence_label": finding.get("confidence_label"),
                }
                for finding in context.get("findings") or []
            ],
            "options": [
                {
                    "text": option.get("text"),
                    "status_label": option.get("status_label"),
                }
                for option in context.get("options") or []
            ],
        }
    )
    return item


def _locator_label(raw: Any) -> str | None:
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    label = str(data.get("region_label") or data.get("note") or "").strip()
    kind = str(data.get("kind") or "").strip()
    if kind == "snapshot" and not label:
        return "快照摘录"
    return label or None


def _superseded_source_ids(
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


def _block_rounds(
    repository: SqliteRepository, project_id: str
) -> dict[str, int]:
    """这一段当前这一版是第几轮收下的；没有收下记录就留空。"""
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT r.deliverable_block_id AS block_id, r.round_index AS round_index
            FROM deliverable_block_revisions r
            JOIN deliverable_blocks b ON b.id = r.deliverable_block_id
            WHERE b.project_id = ? AND r.adopted = 1
            ORDER BY r.round_index, r.version
            """,
            (project_id,),
        ).fetchall()
    latest: dict[str, int] = {}
    for row in rows:
        latest[str(row["block_id"])] = int(row["round_index"] or 1)
    return latest


def _source_lines(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每份来源出现一次：名称 + 链接或文件名，外加口径标记。

    正文才是主体，主张全文不再复述进导出；但「客户提供」和「未独立核实」这两个
    标记必须留着——收下不等于已核实，客户来源也不等于外部独立核实。
    """
    order: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for claim in claims:
        source = claim.get("source") or {}
        key = str(source.get("id") or source.get("title") or "").strip()
        if not key:
            continue
        if key not in rows:
            order.append(key)
            rows[key] = {
                "title": str(source.get("title") or "未命名材料"),
                "locator": str(
                    source.get("original_url") or source.get("file_name") or ""
                ).strip()
                or None,
                "limitation": str(source.get("limitation") or "").strip() or None,
                "client_provided": False,
                "manager_feedback": False,
                "independently_verified": True,
            }
        if claim.get("provenance_scope") == "client_provided":
            rows[key]["client_provided"] = True
        if claim.get("provenance_scope") == "manager_feedback":
            rows[key]["manager_feedback"] = True
        if claim.get("independently_verified") is False:
            rows[key]["independently_verified"] = False
    for row in rows.values():
        notes = []
        if row["client_provided"]:
            notes.append("客户提供")
        if row["manager_feedback"]:
            notes.append("经理反馈")
        if not row["independently_verified"]:
            notes.append("未独立核实")
        row["note"] = "，".join(notes) or None
    return [rows[key] for key in order]


def _header_fields(
    project: dict[str, Any],
    project_override: dict[str, Any] | None,
    omitted_titles: list[str],
) -> dict[str, Any]:
    reason = None
    handling_label = None
    if project_override:
        reason = project_override.get("reason")
        handling = project_override.get("handling")
        handling_label = _OVERRIDE_LABELS.get(handling, handling)
    return {
        "project_id": project["id"],
        "project_name": project["name"],
        "project_override_reason": reason,
        "project_override_label": handling_label,
        "omitted_titles": list(omitted_titles),
    }


def _filename(project_name: str) -> str:
    cleaned = "".join(
        character if character not in '\\/:*?"<>|' else "_"
        for character in str(project_name).strip()
    )
    return f"{cleaned or 'internal-draft'}.md"
