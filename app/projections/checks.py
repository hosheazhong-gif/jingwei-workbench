from __future__ import annotations

import re
from typing import Any

_NUMBER = re.compile(
    r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*[%％]\+?|\+)?(?:亿|万)?"
)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_ORG = re.compile(
    r"[\u4e00-\u9fff]{2,16}(?:有限公司|股份有限公司|集团|产业园|管理局|委员会|研究院)"
)
_SENTENCE = re.compile(r"[^。！？\n]+[。！？]?")
# 「材料没说什么」这一类说法。它们本身有价值（尤其尽调），但同一个缺口反复说
# 就是灌水。实测一节稿里这四个词加起来出现了 10 次。
_GAP_WORDS = ("未说明", "未披露", "未给出", "未提供", "未提及")
_SUMMARY_HEAD = re.compile(r"^\s*[一二三四五六七八九十]+、\s*小结\s*$", re.M)
# 6 字片段是中文里够长的重合单位；重叠的片段先并成整句再数，
# 否则「公司自建供应」和「司自建供应链」会被算成两处。
_REPEAT_GRAM = 6
_REPEAT_MIN_PHRASES = 2


def unsourced_numbers(text: str, claim_texts: list[str]) -> list[dict[str, Any]]:
    """稿里的数字若不在已挂主张中，标为无来源。"""
    joined = "\n".join(claim_texts)
    joined_core = joined.replace(",", "")
    found: list[dict[str, Any]] = []
    for match in _NUMBER.finditer(text or ""):
        token = match.group(0)
        core = re.sub(r"[%％+\s亿万]", "", token)
        if not core or _YEAR.fullmatch(core):
            continue
        after = (text or "")[match.end() : match.end() + 1]
        if (
            len(core) <= 2
            and not re.search(r"[%％亿万]", token)
            and after in {".", "、", ")", "）", "．"}
        ):
            continue
        if core in joined_core or token in joined:
            continue
        found.append(
            {
                "text": token,
                "start": match.start(),
                "end": match.end(),
                "kind": "number",
            }
        )
    return found


def unsourced_orgs(text: str, claim_texts: list[str]) -> list[dict[str, Any]]:
    """稿里的机构名若不在已挂主张中，标为无来源。"""
    joined = "\n".join(claim_texts)
    found: list[dict[str, Any]] = []
    for match in _ORG.finditer(text or ""):
        token = match.group(0)
        if token in joined:
            continue
        found.append(
            {
                "text": token,
                "start": match.start(),
                "end": match.end(),
                "kind": "org",
            }
        )
    return found


def unsourced_marks(text: str, claim_texts: list[str]) -> list[dict[str, Any]]:
    marks = unsourced_numbers(text, claim_texts) + unsourced_orgs(text, claim_texts)
    marks.sort(key=lambda item: (item["start"], item["end"]))
    return _without_overlap(marks)


def novel_claims(text: str, marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """含无来源数字或机构名的句子，视为写成稿时新冒出的主张。"""
    found: list[dict[str, Any]] = []
    for match in _SENTENCE.finditer(text or ""):
        sentence = match.group(0).strip()
        if not sentence:
            continue
        if any(match.start() <= item["start"] < match.end() for item in marks):
            found.append(
                {
                    "text": sentence,
                    "start": match.start(),
                    "end": match.end(),
                }
            )
    return found


def repeated_phrases(text: str) -> list[dict[str, Any]]:
    """同一个意思换着说了几遍。

    两种最常见的灌水，各报一条：
    1. 缺口散着写——每条事实后面都挂一句「报告未说明…因此无法…」；
    2. 小结复述正文——把上面已经逐条写过的数字和结论再抄一遍。

    只提示，不拦截：稿仍然可以照收，人自己决定要不要让模型重写一版。
    """
    body = text or ""
    found: list[dict[str, Any]] = []

    gap_lines = [
        line
        for line in body.splitlines()
        if any(word in line for word in _GAP_WORDS)
    ]
    item_lines = [line for line in body.splitlines() if re.match(r"^\s*\d+[.．、]", line)]
    if len(item_lines) >= 4 and len(gap_lines) * 2 > len(item_lines):
        found.append(
            {
                "kind": "gap_spread",
                "text": f"{len(item_lines)} 条正文里有 {len(gap_lines)} 条在说材料没说什么",
                "hint": "缺口集中到每小节最后一条说一次，正文条目只写材料说了什么",
            }
        )

    parts = _SUMMARY_HEAD.split(body)
    if len(parts) >= 2:
        head, tail = "".join(parts[:-1]), parts[-1]
        head_core = re.sub(r"\s", "", head)
        tail_core = re.sub(r"\s", "", tail)
        grams = {
            head_core[i : i + _REPEAT_GRAM]
            for i in range(max(0, len(head_core) - _REPEAT_GRAM))
        }
        hits = [
            i
            for i in range(max(0, len(tail_core) - _REPEAT_GRAM))
            if tail_core[i : i + _REPEAT_GRAM] in grams
        ]
        echoed = _merge_runs(tail_core, hits)
        if len(echoed) >= _REPEAT_MIN_PHRASES:
            found.append(
                {
                    "kind": "summary_echo",
                    "text": "小结里有 " + str(len(echoed)) + " 处照抄了正文",
                    "hint": "小结只说答到哪一步、还缺什么、下一步补什么，不要复述正文的数字",
                    "samples": echoed[:3],
                }
            )
    return found


def _merge_runs(text: str, starts: list[int]) -> list[str]:
    """把连着的片段并成整句，重叠的算一处。"""
    phrases: list[str] = []
    begin = None
    end = None
    for start in starts:
        if begin is None:
            begin, end = start, start + _REPEAT_GRAM
        elif start <= end:
            end = start + _REPEAT_GRAM
        else:
            phrases.append(text[begin:end])
            begin, end = start, start + _REPEAT_GRAM
    if begin is not None:
        phrases.append(text[begin:end])
    return phrases


def mechanical_checks(
    text: str,
    claims: list[dict[str, Any]],
    superseded: set[str],
) -> dict[str, Any]:
    claim_texts = [str(item.get("text") or "") for item in claims]
    marks = unsourced_marks(text or "", claim_texts)
    client_as_verified = []
    feedback_as_evidence = []
    macro_as_demand = []
    stale = False
    for claim in claims:
        source = claim.get("source") or {}
        source_id = source.get("id")
        delivery = str(claim.get("delivery_rule") or "")
        client = "客户提供" in delivery
        macro = "不单独证明项目需求" in delivery or "宏观" in str(
            source.get("limitation") or ""
        )
        if (
            client
            and claim.get("verification_status") == "corroborated"
            and not claim.get("independently_verified")
        ):
            client_as_verified.append(
                {
                    "claim_id": claim["id"],
                    "text": claim.get("text"),
                    "source_id": source_id,
                }
            )
        if macro and re.search(r"项目需求|本项目.*需求|证明需求", text or ""):
            macro_as_demand.append(
                {
                    "claim_id": claim["id"],
                    "text": claim.get("text"),
                    "source_id": source_id,
                }
            )
        # 经理反馈是内部指示，不是外部证据：核验推不上去，也不能标成已独立核实。
        if str(claim.get("provenance_scope") or "") == "manager_feedback" and (
            claim.get("independently_verified")
            or claim.get("verification_status") in {"source_checked", "corroborated"}
        ):
            feedback_as_evidence.append(
                {
                    "claim_id": claim["id"],
                    "text": claim.get("text"),
                    "source_id": source_id,
                }
            )
        if claim.get("verification_status") == "stale":
            stale = True
        if source_id and source_id in superseded:
            stale = True
    return {
        "unsourced_numbers": [item for item in marks if item.get("kind") == "number"],
        "unsourced_orgs": [item for item in marks if item.get("kind") == "org"],
        "unsourced_marks": marks,
        "novel_claims": novel_claims(text or "", marks),
        "client_as_verified": client_as_verified,
        "feedback_as_evidence": feedback_as_evidence,
        "macro_as_demand": macro_as_demand,
        "repeated_phrases": repeated_phrases(text or ""),
        "stale": stale,
    }


def _without_overlap(marks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    last_end = -1
    for item in marks:
        if item["start"] < last_end:
            continue
        kept.append(item)
        last_end = item["end"]
    return kept
