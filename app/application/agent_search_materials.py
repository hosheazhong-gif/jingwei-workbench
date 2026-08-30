"""按本轮问题做多轮检索的研究代理（docs/11 的 B 组：单一研究 Agent）。

与固定流程 `search_materials.py` 的唯一区别是**怎么决定检索词**：
固定流程一次性拟好至多 3 条就去搜；这里让模型看着上一轮的结果决定下一轮换什么词，
搜到够用或者判断再搜也没用就自己停。

**其余全部不变，这是它能跟固定流程做 A/B 的前提：**
- 只写 CandidateSource，不升为来源、不改稿、不改核验（收尾同样跑 `_assert_unchanged`）；
- 工具只有一个，而且只读：搜索。代理不能升来源、不能删候选、不能执行代码；
- 人工闸门一道不少——候选仍然要人点开才存得下快照，才谈得上升为来源。

护栏抄 `chainsys/core/agent.py` 的双重护栏（那份是本机已经跑通的参考实现）：
最大轮次 + 每轮观测截断，防失控烧额度。动作协议用纯 JSON，不依赖厂商
function-calling，任何 OpenAI 兼容的模型都能跑。

**做 A/B 时注意两个参数**（第一次跑就是栽在这上面）：
- `seed_queries`：第 1 轮直接用固定流程那批词，之后才让模型换词。
  不给种子的话，两组从第一轮就搜的是不同的词，比出来的是"两套问法"而不是"两种策略"。
- `max_candidates`：默认上限太低会让代理第 1 轮就顶格退出，第二轮根本不发生，
  换词这个唯一卖点就没机会展示。两组必须设同一个值。
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.adapters.sqlite_repository import SqliteRepository
from app.application.candidate_source import CandidateSourceError, capture_web_candidate
from app.projections.brief import build_brief_projection
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection

# 若本模块进主线，这几个应从 search_materials 提到共用模块，不该跨模块引私有函数。
from app.application.search_materials import (  # noqa: PLC2701
    MAX_NEW_CANDIDATES,
    MAX_QUERY_CHARS,
    SearchMaterialsError,
    _all_block_texts,
    _all_claim_flags,
    _assert_unchanged,
    _candidate_note,
    _confirmation_message,
    _existing_urls,
)

MAX_ROUNDS = 4
MAX_QUERIES_PER_ROUND = 2
OBS_LIMIT = 1200


class AgentSearchError(SearchMaterialsError):
    """与固定流程共用错误类型，界面上的提示措辞才不会分叉。"""


class AgentBrain(Protocol):
    """最小协议：给一段提示，返回一段文本。解析由本模块负责。"""

    key: str

    def decide(self, prompt: str) -> str: ...


_SYSTEM = """你在替一名咨询顾问按一条研究问题找公开网页材料。
每轮只输出一个 JSON 对象，不要任何其他文字：
  继续搜：{"思考":"一句话","动作":"搜索","检索词":["词1","词2"]}
  停止：  {"思考":"一句话","动作":"结束","理由":"为什么不用再搜"}

规则：
①每轮最多给 %d 条检索词，每条不超过 %d 字；
②不要重复已经试过的词，换词要说得出换的是哪个维度（口径/时间/地域/同义词/上位词）；
③已经拿到足够覆盖这条问题的候选，或者判断公开网上就是没有，就尽快结束——轮次是成本；
④你只能搜。你不能决定哪条材料算数，那是人的事。

本轮问题：%s
这轮要决定：%s
已试过的检索词：%s
已拿到的候选标题：%s
剩余轮次：%d

现在输出你的 JSON："""


def agent_search_project_materials(
    repository: SqliteRepository,
    project_id: str,
    *,
    question_id: str | None = None,
    search_adapter: Any = None,
    brain: AgentBrain | None = None,
    max_rounds: int = MAX_ROUNDS,
    max_candidates: int | None = None,
    seed_queries: Sequence[str] | None = None,
    on_step: Any = None,
) -> dict[str, Any]:
    """多轮检索。返回结构与 `search_project_materials` 对齐，另加 `transcript`。"""
    if not repository.has_project(project_id):
        raise AgentSearchError(f"项目 {project_id} 不存在")
    cap = int(max_candidates or MAX_NEW_CANDIDATES)
    seeds = [str(item).strip()[:MAX_QUERY_CHARS] for item in (seed_queries or []) if str(item).strip()]
    if brain is None and not seeds:
        raise AgentSearchError("还没接模型。先在「连接模型」里填好 API Key，再用代理搜。")
    if search_adapter is None:
        from app.adapters.http_search import resolve_search_adapter

        try:
            search_adapter = resolve_search_adapter()
        except Exception as error:  # noqa: BLE001
            raise AgentSearchError(str(error)) from error

    question_id = (str(question_id).strip() or None) if question_id else None
    before_flags = _all_claim_flags(repository)
    before_drafts = _all_block_texts(repository)
    before_sources = repository.list_source_ids(project_id)

    brief = build_brief_projection(repository, project_id)
    current = int(brief.get("current_round") or 1)
    active = [
        item
        for item in brief["questions"]
        if item["status"] != DEFERRED_STATUS and int(item.get("round_index") or 1) == current
    ]
    if active and not question_id:
        raise AgentSearchError("先点开左边要搜的那条问题，再搜。没有写入候选，也没有改稿。")

    question_text = _question_text(brief, question_id)
    decision = str(brief.get("round_decision") or brief.get("brief") or "").strip()

    existing = _existing_urls(repository, project_id)
    tried: list[str] = []
    added: list[dict[str, Any]] = []
    transcript: list[dict[str, Any]] = []
    started = time.monotonic()

    for index in range(max_rounds):
        step: dict[str, Any] = {"轮": index + 1}

        # 第 1 轮：给了种子就用种子，A/B 才是从同一起点出发。
        if index == 0 and seeds:
            queries = _clean_queries(seeds, tried, limit=len(seeds))
            step["思考"] = "沿用固定流程的种子检索词"
            step["来源"] = "seed"
        else:
            if brain is None:
                break
            prompt = _SYSTEM % (
                MAX_QUERIES_PER_ROUND,
                MAX_QUERY_CHARS,
                question_text,
                decision or "（本轮未写）",
                "；".join(tried) or "（还没搜过）",
                "；".join(item.get("title") or item.get("url", "") for item in added) or "（还没有）",
                max_rounds - index,
            )
            try:
                raw = brain.decide(prompt)
            except Exception as error:  # noqa: BLE001
                step["错误"] = f"模型调用失败：{str(error)[:200]}"
                transcript.append(step)
                _emit(on_step, step)
                break

            parsed = _parse(raw)
            if parsed is None:
                # 抄 chainsys：不重试调用，把失败写回上下文，消耗一轮。
                step["错误"] = "输出不是合法 JSON，已提示重来"
                tried.append("(上一轮输出非法，已提示重来)")
                transcript.append(step)
                _emit(on_step, step)
                continue

            step["思考"] = str(parsed.get("思考", ""))[:120]
            if parsed.get("动作") == "结束":
                step["动作"] = "结束"
                step["理由"] = str(parsed.get("理由", ""))[:200]
                transcript.append(step)
                _emit(on_step, step)
                break
            queries = _clean_queries(parsed.get("检索词"), tried)

        if not queries:
            step["错误"] = "没有给出可用的新检索词"
            transcript.append(step)
            _emit(on_step, step)
            continue

        step["动作"] = "搜索"
        step["检索词"] = queries
        observations: list[str] = []
        for query in queries:
            if len(added) >= cap:
                break
            tried.append(query)
            try:
                hits = list(search_adapter.search(query) or [])
            except Exception as error:  # noqa: BLE001
                # 工具报错不打断循环，当成一次观测喂回去（抄 chainsys）。
                observations.append(f"「{query}」检索失败：{str(error)[:200]}")
                continue
            new = _write_candidates(
                repository,
                project_id,
                hits=hits,
                query=query,
                question_id=question_id,
                existing=existing,
                added=added,
                cap=cap,
            )
            observations.append(f"「{query}」返回 {len(hits)} 条，新增候选 {new} 条")

        step["观测"] = "；".join(observations)[:OBS_LIMIT]
        transcript.append(step)
        _emit(on_step, step)
        if len(added) >= cap:
            transcript.append(
                {"轮": index + 1, "动作": "结束", "理由": f"已达候选上限 {cap}（未必是模型想停）"}
            )
            break

    if not tried:
        raise AgentSearchError("代理没有发出任何检索。没有写入候选，也没有改稿。")

    # 与固定流程同一条不变量：这一步只许新增候选。
    _assert_unchanged(repository, project_id, before_flags, before_drafts, before_sources)

    workbench = build_workbench_projection(repository, project_id)
    queries_used = [item for item in tried if not item.startswith("(")]
    return {
        "project_id": project_id,
        "queries": queries_used,
        "added": added,
        "added_count": len(added),
        "skipped_count": 0,
        "workbench": workbench,
        # 代理特有：给 A/B 用的过程数据
        "rounds_used": len(transcript),
        "hit_candidate_cap": len(added) >= cap,
        "elapsed_seconds": round(time.monotonic() - started, 2),
        "transcript": transcript,
        "confirmation": {
            "recorded": True,
            "record_kind": "search_candidates",
            "verification_status_changed": False,
            "deliverable_changed": False,
            "source_created": False,
            "message": _confirmation_message(len(added), 0, queries_used),
        },
    }


# ── 内部 ──

def _emit(on_step: Any, step: Mapping[str, Any]) -> None:
    if on_step:
        on_step(dict(step))


def _parse(raw: str) -> dict[str, Any] | None:
    text = str(raw or "")
    try:
        start, end = text.index("{"), text.rindex("}")
        value = json.loads(text[start : end + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _clean_queries(value: Any, tried: Sequence[str], *, limit: int | None = None) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    ceiling = limit or MAX_QUERIES_PER_ROUND
    seen = {item.strip() for item in tried}
    out: list[str] = []
    for item in value:
        query = str(item or "").strip()[:MAX_QUERY_CHARS]
        if query and query not in seen and query not in out:
            out.append(query)
        if len(out) >= ceiling:
            break
    return out


def _question_text(brief: Mapping[str, Any], question_id: str | None) -> str:
    for item in brief.get("questions") or []:
        if question_id and str(item.get("id")) == question_id:
            return str(item.get("text") or "")
    return str(brief.get("brief") or "")


def _write_candidates(
    repository: SqliteRepository,
    project_id: str,
    *,
    hits: Sequence[Any],
    query: str,
    question_id: str | None,
    existing: set[str],
    added: list[dict[str, Any]],
    cap: int,
) -> int:
    count = 0
    for hit in hits:
        if len(added) >= cap:
            break
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("url") or "").strip()
        if not url or url in existing:
            continue
        title = str(hit.get("title") or "").strip() or None
        note = _candidate_note(query, str(hit.get("snippet") or "").strip())
        try:
            captured = capture_web_candidate(
                repository,
                project_id,
                url=url,
                title=title,
                note=note,
                question_id=question_id,
            )
        except CandidateSourceError as error:
            if "已在候选列表" in str(error) or "必须是 http" in str(error):
                existing.add(url)
                continue
            raise AgentSearchError(str(error)) from error
        added.append(captured["candidate"])
        existing.add(url)
        count += 1
    return count
