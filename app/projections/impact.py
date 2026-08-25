from __future__ import annotations

import re
from typing import Any, Sequence

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.numbers import number_tokens

_DIGITS = re.compile(r"\d")
_MAGNITUDE = re.compile(r"[亿万]")

_LIMITATION = "只包含显式关系能够证明的影响，不会自动改写内部稿。"

_CROSS_LIMITATION = (
    "只列已经挂着的关系：这一版改掉的数字、同一条依据、同一份材料、同一个数字。"
    "列出来不等于那几节就错了，也不会自动去改它们——改不改仍然是人的事。"
)
_CROSS_EMPTY = "没有别的小节跟这一节共用依据或数字。改这一节，别处不用跟着看。"
_KIND_ORDER = {
    "changed_number": 0,
    "same_claim": 1,
    "same_source": 2,
    "same_number": 3,
}
_CLAIM_SNIPPET = 24


def cross_section_impact(blocks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """改一节，别的哪几节要跟着看一眼。

    三类咨询活真正累人的地方是「改」不是「写」：一个数字在这一版被改掉了，
    稿里别处还照旧写着老口径，人自己是记不住的。这里沿已经挂着的关系算——
    这一版改掉的数字、同一条依据、同一份材料、同一个数字——算不出来的
    一律不猜、不写，也不会自动去动别的小节。
    """
    facts = [_block_facts(item) for item in blocks or []]
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(facts):
        related = []
        for other_index, other in enumerate(facts):
            if other_index == index:
                continue
            reasons = _reasons(item, other)
            if not reasons:
                continue
            related.append(
                {
                    "block_id": other["id"],
                    "title": other["title"],
                    "reasons": reasons,
                    "strength": _KIND_ORDER[reasons[0]["kind"]],
                }
            )
        related.sort(key=lambda row: (row["strength"], row["title"]))
        result[str(item["id"])] = {
            "related": related,
            "total": len(related),
            "changed_numbers": item["changed"],
            "heading": _heading(item, related),
            "limitation": _CROSS_LIMITATION,
        }
    return result


def _heading(item: dict[str, Any], related: list[dict[str, Any]]) -> str:
    hit = [row for row in related if row["reasons"][0]["kind"] == "changed_number"]
    if hit:
        return (
            "这一版动了 "
            + str(len(item["changed"]))
            + " 个数字，其中有 "
            + str(len(hit))
            + " 节还照旧写着老数。收下这一版之前先看一眼。"
        )
    if item["changed"] and not related:
        return "这一版动了数字，但别的小节没写过这几个数。"
    if not related:
        return _CROSS_EMPTY
    return "改了这一节，下面这 " + str(len(related)) + " 节用的是同一批依据，一起看一眼。"


def _block_facts(block: dict[str, Any]) -> dict[str, Any]:
    claim_sources = block.get("claim_sources") or []
    claims: dict[str, str] = {}
    sources: dict[str, str] = {}
    for item in claim_sources:
        claim_id = item.get("claim_id")
        if claim_id:
            claims[str(claim_id)] = str(item.get("claim_text") or "")
        source_id = item.get("source_id")
        if source_id:
            sources[str(source_id)] = str(item.get("source_title") or "")
    numbers = _identifying_numbers(block.get("current_text") or "")
    pending = block.get("pending_revision") or {}
    body = pending.get("body")
    # 这一版把哪几个数字去掉或改掉了。没有待收的改稿就没有这一项。
    changed = (
        [item for item in numbers if item not in _identifying_numbers(str(body))]
        if body is not None
        else []
    )
    return {
        "id": block.get("id"),
        "title": block.get("title") or "",
        "claims": claims,
        "sources": sources,
        "numbers": numbers,
        "changed": changed,
    }


def _identifying_numbers(text: str) -> list[str]:
    return [
        token
        for token, _start, _end in number_tokens(text or "")
        if _is_identifying_number(token)
    ]


def _is_identifying_number(token: str) -> bool:
    """只有「认得出是同一个事实」的数字才算共用。

    真机上第一版把 70% 也算进去，结果每一节都跟其余每一节相关——
    一个哪儿都亮的提示等于没提示。粗整数和整百分比在稿里到处都是，
    撞上多半是巧合；带亿／万的量级，或者三位以上有效数字，才像同一个数。
    """
    digits = _DIGITS.findall(token)
    if not digits:
        return False
    if _MAGNITUDE.search(token):
        return len(digits) >= 2
    return len(digits) >= 3


def _reasons(item: dict[str, Any], other: dict[str, Any]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    for number in item["changed"]:
        if number in other["numbers"]:
            reasons.append(
                {
                    "kind": "changed_number",
                    "label": "这一版改掉的数字，那节还写着",
                    "detail": number,
                }
            )
    changed = set(item["changed"])
    for claim_id in item["claims"]:
        if claim_id in other["claims"]:
            text = item["claims"][claim_id] or other["claims"][claim_id]
            reasons.append(
                {
                    "kind": "same_claim",
                    "label": "同一条依据",
                    "detail": _snip(text) or claim_id,
                }
            )
    for source_id in item["sources"]:
        if source_id in other["sources"]:
            reasons.append(
                {
                    "kind": "same_source",
                    "label": "同一份材料",
                    "detail": item["sources"][source_id] or source_id,
                }
            )
    for number in item["numbers"]:
        if number in changed:
            continue
        if number in other["numbers"]:
            reasons.append(
                {"kind": "same_number", "label": "同一个数字", "detail": number}
            )
    reasons.sort(key=lambda row: _KIND_ORDER[row["kind"]])
    return reasons


def _snip(text: str) -> str:
    body = " ".join(str(text or "").split())
    if len(body) <= _CLAIM_SNIPPET:
        return body
    return body[:_CLAIM_SNIPPET] + "…"


def build_impact_preview(
    repository: SqliteRepository, source_id: str
) -> dict[str, Any]:
    """临时影响范围：沿 Source → Claim → DeliverableBlock 计算；

    存在 Finding / Option 显式外键时再继续传播。不持久化，不猜测未关联段落。
    """
    with repository.connect() as connection:
        source = connection.execute(
            "SELECT id, project_id, title FROM sources WHERE id = ?",
            (source_id,),
        ).fetchone()
        if source is None:
            raise KeyError(f"来源 {source_id} 不存在")

        superseded = _superseded_sources(
            connection, source_id, str(source["project_id"])
        )
        related_ids = [source_id, *[item["id"] for item in superseded]]
        claims = _claims_for_sources(connection, related_ids)
        claim_ids = [row["id"] for row in claims]
        findings = _findings_for_sources_and_claims(
            connection, related_ids, claim_ids
        )
        finding_ids = [row["id"] for row in findings]
        options = _options_for_explicit_links(
            connection, related_ids, claim_ids, finding_ids
        )
        option_ids = [row["id"] for row in options]
        blocks = _blocks_for_explicit_links(
            connection, claim_ids, finding_ids, option_ids
        )

    return {
        "source": {"id": source["id"], "title": source["title"]},
        "superseded_sources": superseded,
        "claims": claims,
        "findings": findings,
        "options": options,
        "deliverable_blocks": blocks,
        "limitation": _LIMITATION,
    }


def _superseded_sources(
    connection: Any, source_id: str, project_id: str
) -> list[dict[str, str]]:
    chain: list[dict[str, str]] = []
    seen = {source_id}
    current_id = source_id
    while True:
        row = connection.execute(
            """
            SELECT supersedes_source_id FROM sources
            WHERE id = ? AND project_id = ?
            """,
            (current_id, project_id),
        ).fetchone()
        next_id = row["supersedes_source_id"] if row else None
        if not next_id or next_id in seen:
            break
        previous = connection.execute(
            "SELECT id, title FROM sources WHERE id = ? AND project_id = ?",
            (next_id, project_id),
        ).fetchone()
        if previous is None:
            break
        chain.append({"id": previous["id"], "title": previous["title"]})
        seen.add(next_id)
        current_id = next_id
    return chain


def _claims_for_sources(
    connection: Any, source_ids: Sequence[str]
) -> list[dict[str, str]]:
    source_sql, source_params = _in("c.source_id", source_ids)
    excerpt_sql, excerpt_params = _in("e.source_id", source_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT c.id, c.text
        FROM claims c
        LEFT JOIN claim_evidence ce ON ce.claim_id = c.id
        LEFT JOIN evidence_excerpts e ON e.id = ce.evidence_excerpt_id
        WHERE {source_sql} OR {excerpt_sql}
        ORDER BY c.rowid
        """,
        (*source_params, *excerpt_params),
    )
    return [{"id": row["id"], "text": row["text"]} for row in rows]


def _findings_for_sources_and_claims(
    connection: Any, source_ids: Sequence[str], claim_ids: Sequence[str]
) -> list[dict[str, str]]:
    source_sql, source_params = _in("fs.source_id", source_ids)
    claim_sql, claim_params = _in("fc.claim_id", claim_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT f.id, f.text
        FROM findings f
        LEFT JOIN finding_sources fs ON fs.finding_id = f.id
        LEFT JOIN finding_claims fc ON fc.finding_id = f.id
        WHERE {source_sql} OR {claim_sql}
        ORDER BY f.rowid
        """,
        (*source_params, *claim_params),
    )
    return [{"id": row["id"], "text": row["text"]} for row in rows]


def _options_for_explicit_links(
    connection: Any,
    source_ids: Sequence[str],
    claim_ids: Sequence[str],
    finding_ids: Sequence[str],
) -> list[dict[str, str]]:
    """当前 schema 没有 Source/Claim/Finding → Option 表，故无法从材料传播到方向。"""
    del connection, source_ids, claim_ids, finding_ids
    return []


def _blocks_for_explicit_links(
    connection: Any,
    claim_ids: Sequence[str],
    finding_ids: Sequence[str],
    option_ids: Sequence[str],
) -> list[dict[str, str]]:
    claim_sql, claim_params = _in("dbc.claim_id", claim_ids)
    finding_sql, finding_params = _in("dbf.finding_id", finding_ids)
    option_sql, option_params = _in("dbo.option_id", option_ids)
    rows = connection.execute(
        f"""
        SELECT DISTINCT b.id, b.title
        FROM deliverable_blocks b
        LEFT JOIN deliverable_block_claims dbc
            ON dbc.deliverable_block_id = b.id
        LEFT JOIN deliverable_block_findings dbf
            ON dbf.deliverable_block_id = b.id
        LEFT JOIN deliverable_block_options dbo
            ON dbo.deliverable_block_id = b.id
        WHERE {claim_sql} OR {finding_sql} OR {option_sql}
        ORDER BY b.rowid
        """,
        (*claim_params, *finding_params, *option_params),
    )
    return [{"id": row["id"], "title": row["title"]} for row in rows]


def _in(column: str, ids: Sequence[str]) -> tuple[str, tuple[str, ...]]:
    if not ids:
        return "0", ()
    placeholders = ", ".join("?" * len(ids))
    return f"{column} IN ({placeholders})", tuple(ids)
