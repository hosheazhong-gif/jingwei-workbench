"""稿里每一个数字，逐个说清它出自哪条已挂原话。

模型编数字比编论述隐蔽得多：一段论述读着不对，人能看出来；一个「19.1 亿元」
混在通顺的句子里，人不会怀疑。所以数字要单独列成一张表，一个一个对。

这张表只读已经批准的正文和已挂原话，不改任何东西，也不拦截导出——
标出来之后收不收、导不导，仍然是人的事（PRD 第 8.3 节的一贯做法）。
"""

from __future__ import annotations

import re
from typing import Any

# 跟机械检查用同一套数字识别，避免两处对「什么算一个数字」的看法不一致
from app.projections.checks import _NUMBER, _YEAR

_STRIP = re.compile(r"[%％+\s亿万]")
_CONTEXT = 18


def number_tokens(text: str) -> list[tuple[str, int, int]]:
    """正文里算得上数据的数字，按出现顺序、去重。年份和条目编号不算。

    「哪些算一个数字」只在这里判一次：数字清单和跨节影响都用它，
    免得两处对同一段正文数出不一样的数字。
    """
    body = text or ""
    found: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for match in _NUMBER.finditer(body):
        token = match.group(0)
        core = _STRIP.sub("", token)
        if not core or _YEAR.fullmatch(core):
            continue
        if _looks_like_a_list_marker(body, match, token, core):
            continue
        if token in seen:
            continue
        seen.add(token)
        found.append((token, match.start(), match.end()))
    return found


def number_manifest(
    text: str, claim_sources: list[dict[str, Any]]
) -> dict[str, Any]:
    """把正文里的数字逐个列出来，标明在哪条原话里找得到。"""
    body = text or ""
    entries: list[dict[str, Any]] = []
    for token, start, end in number_tokens(body):
        core = _STRIP.sub("", token)
        hit = _find_in_excerpts(token, core, claim_sources)
        entries.append(
            {
                "number": token,
                "context": _around(body, start, end),
                "found_in_excerpt": hit is not None,
                "source_title": (hit or {}).get("source_title"),
                "source_id": (hit or {}).get("source_id"),
                "excerpt": (hit or {}).get("excerpt"),
            }
        )
    missing = [item for item in entries if not item["found_in_excerpt"]]
    return {
        "numbers": entries,
        "total": len(entries),
        "unsourced": len(missing),
        "limitation": (
            "逐个对的是「这个数字在已挂原话里出不出现」，不是「这个数字对不对」。"
            "挂上了也可能是材料本身写错；没挂上就是模型自己写出来的，收下前先看一眼。"
        ),
    }


def _looks_like_a_list_marker(
    body: str, match: re.Match[str], token: str, core: str
) -> bool:
    """「1.」「2、」这种条目编号不是数据，不进表。"""
    after = body[match.end() : match.end() + 1]
    return (
        len(core) <= 2
        and not re.search(r"[%％亿万]", token)
        and after in {".", "、", ")", "）", "．"}
    )


def _find_in_excerpts(
    token: str, core: str, claim_sources: list[dict[str, Any]]
) -> dict[str, Any] | None:
    for item in claim_sources or []:
        excerpt = str(item.get("excerpt") or "")
        if not excerpt:
            continue
        if token in excerpt or core in excerpt.replace(",", ""):
            return {
                "source_title": item.get("source_title"),
                "source_id": item.get("source_id"),
                "excerpt": excerpt[:60],
            }
    return None


def _around(body: str, start: int, end: int) -> str:
    left = max(0, start - _CONTEXT)
    right = min(len(body), end + _CONTEXT)
    prefix = "…" if left > 0 else ""
    suffix = "…" if right < len(body) else ""
    return prefix + " ".join(body[left:right].split()) + suffix
