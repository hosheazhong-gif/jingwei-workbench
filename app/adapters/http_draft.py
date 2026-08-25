from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.adapters.unconfigured_draft import UnconfiguredDraftAdapter
from app.application.draft_suggestion import DraftSuggestionError

DEFAULT_DRAFT_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_DRAFT_MODEL = "gpt-4o-mini"
DEFAULT_DRAFT_PROVIDER = "openai"
MAX_PROPOSAL_CHARS = 400
MAX_REVISION_CHARS = 2500
DRAFT_PROVIDERS = {
    "openai": {
        "url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
    "deepseek": {
        "url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
    },
    "moonshot": {
        "url": "https://api.moonshot.cn/v1/chat/completions",
        "model": "moonshot-v1-8k",
    },
    "qwen": {
        "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
    },
    "glm": {
        "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
    },
}
_PROVIDER_ALIASES = {
    "kimi": "moonshot",
    "zhipu": "glm",
    "dashscope": "qwen",
}
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
_SYSTEM_PROMPT = """你只先拟候选，不是成稿，也不是来源。
根据段落标题和已挂依据，最多给出一条总判断（kind=finding）和一条可试方向（kind=option）。
规则：
- 不得发明数字、机构名、文件或已核实结论
- 没有依据时，总判断只能写材料不足，方向只能写下一步该补什么
- 不得写成内部稿正文，不得自称来源或证据
- 只输出 JSON：{"proposals":[{"kind":"finding","text":"..."},{"kind":"option","text":"..."}]}
"""
_SEARCH_QUERY_PROMPT = """你只出检索词，不要给链接，不要写稿。
根据本轮要决定什么和本轮问题，最多给出 3 条适合公开网页搜索的检索词。
规则：
- 不得输出 URL、域名或把某页写成来源
- 不得发明具体统计数字
- 检索词要短，像人会打进搜索框的词，不要写成句子
- 只输出 JSON：{"queries":["...","..."]}
"""
_ROUND_QUESTION_PROMPT = """你只拆本轮问题，不写稿，不是来源。
第一轮：根据经理原话和这轮要决定什么，给出 3 到 5 条本轮就够用的问题。
第二轮起：不要重拆经理原话，改为审阅上一轮——读上一轮的稿和上一轮问题，
找出哪条只答了一半、哪条只列了缺口、哪条被材料推翻，再叠上经理这一轮的反馈，
给出 3 到 5 条这一轮要补的问题。
规则：
- 第二轮起以补上一轮的缺口为主，不要换一批同类问题重问一遍
- 经理反馈是内部指示，不是证据：可以决定这一轮问什么，但不得当成已核实事实
- 上一轮稿里写的「下一步需补充……」是最直接的线索，优先转成问题
- 问题是这轮要回答什么，不是报告目录，也不是完整咨询框架
- 不得发明数字、机构名、公司名单或已核实结论
- 原话没点到、这轮仍可能卡住的缺口，写在 enough_for_now
- 参考标签仅当原话明显同类才可借鉴；禁止把无关模板问题套进来
- 已有问题若与原话无关，不要沿用
- 上一轮已经问过的不要原样重复；除非原话要求换角度
- 每条另给一个 label：8 到 14 字的短名，够人在左栏一眼认出这条问，不要标点结尾
- 每条还要说清这条问题的答案将来落在稿的哪一节（先立骨架再找料）：
  section 写现有节名之一，原样照抄不要改字；现有节都放不下才写一个新节名，
  新节名 6 到 14 字、是稿里的小标题不是问句。**看不出该落哪一节就留空**，不要硬塞
- 只输出 JSON：{"questions":[{"question":"...","label":"...","section":"...","enough_for_now":"..."}]}
"""
_ROUND_DECISION_PROMPT = """你只写这一轮要决定什么，不写稿，不是来源。
读上一轮的稿、上一轮的问题和经理这一轮的反馈，写出这一轮要决定的那一句话。
规则：
- 一句话，不超过 60 字，说清这一轮要替经理定下什么，不是罗列要做的事
- 承接上一轮：上一轮已经答清的不要再当这轮的决定
- 经理反馈是内部指示，这一轮要围着它转，但不得当成已核实事实
- 不得发明数字、机构名、文件或已核实结论
- 只输出 JSON：{"decision":"..."}
"""
_SNAPSHOT_EXCERPT_PROMPT = """你只从快照正文里摘原话，不写稿，不是来源。
根据点开的问题和快照正文，给出 2 到 5 条能回答该问题的原文摘录。
规则：
- 必须是快照里出现过的连续原句或原片段，不得改写、概括、翻译或发明
- 每条不超过 180 字，不要输出 URL，不要写成判断
- 只输出 JSON：{"excerpts":["..."]}
"""
_REVISION_PROMPT = """你写给经理看的这一节候选，不是成稿，也不是来源。
根据本轮要决定什么、点开的问题、现稿、这一节已挂原话来写，只输出一条 kind=revision。
规则：
- 必须靠「这一节已挂原话」回答点开的那条问题，不要只把问题再拆一遍
- 没有已挂原话时不得编造答案
- 不得发明数字、机构名、文件或已核实结论
- 只有已挂原话标明「客户提供」时才能写「据客户提供、口径待补」；公开网页和本机文件不得写成客户提供
- 经理原话不是来源，不能把网页材料说成客户口头
- 未标明独立核实的，不得写成已核实
- 宏观材料不能单独证明项目需求
- 只有「这一节已挂原话」能当这一节的依据；材料匣其他原话只作背景，不要写成已经挂上
- 现稿若标明还是空的，写缺口稿：还缺什么、口头信息怎么带归属、下一步补什么；禁止复述「这一节还没写」
- 现稿若已有内容，按问题和已挂原话改写成可给经理看的一节，不要只在原文外加口径声明
写成整理稿，不是摘要。经理拿到的是内部整理稿，要能当工作底稿读：
- 详略按材料定：已挂原话里有多少可用信息就写多少，不要压成概述。材料给了清单、
  年份、金额、比例、地区、企业名、文件名，就逐条写出来，不要合并成「等」「多项」「若干」
- 版式按论文来。全节先用小标题分小节，小标题写成「一、中央层面的政策口径」这样，
  单独占一行，行内不接正文；材料够多时小标题下再用「（一）」「（二）」分层
- 材料够两个主题就至少写三节小标题；确实只有一个主题时也要至少两节
- 并列事实分条罗列，每条以「1.」「2.」「3.」开头并单独占一行，不要把几条塞进同一段
- 每条至少写成一个完整句子：是什么、出自哪份材料、有什么条件或时限；
  禁止只写一个短语，也禁止整节只有一两句话就收尾
- 每条尽量带上出处名称，便于经理回看
- 换行必须是真实换行（JSON 字符串里写成 \n），不要用「/」「；」或空格代替换行
- 最后另起一节「小结」，两到四句话说这一节回答了点开的那条问题的哪一部分、还差什么；
  不要用「综上所述」「总的来说」开头
- 材料确实很少时，仍要写小标题和分条，并在小结里写清楚材料不足在哪里，不要靠套话凑长
写清楚材料没说什么，但同一个缺口只说一次。实测一节稿 13 条正文里 8 条在说
「材料没说什么」，其中 5 条以「因此无法…」收尾，小结又把正文的数字整串抄一遍：
- 正文的每一条只写材料说了什么。**不要在每条事实后面各挂一句「报告未说明…因此无法…」**
- 一小节里材料没覆盖到的地方，集中写成该小节最后一条「这一小节还缺：……」，一次说完
- 例外：某个数字本身是推算而非披露、或有口径/时点限制时，这一句紧跟着那个数字写，
  因为不写在旁边就会被当成已披露的事实
- 同一个意思不要换一种说法再写一遍；「未说明」「未披露」「未给出」「未提供」是同一件事
- 小结只写三样：这一节答到了点开那条问题的哪一步、最要紧的一到两个缺口、下一步补什么。
  **禁止在小结里复述正文已经逐条写过的数字、比例和企业名**——正文里有，读的人回头看得到
- 不得自称来源或证据
- 只输出 JSON：{"proposals":[{"kind":"revision","text":"..."}]}
"""


class HttpJsonDraftAdapter:
    """OpenAI 兼容 chat completions。只返回先拟候选，不写内部稿。"""

    key = "http_json"

    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        model: str,
        opener: Callable[..., Any] | None = None,
        timeout: int = 30,
        provider: str = DEFAULT_DRAFT_PROVIDER,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._model = model
        self._opener = opener or urlopen
        self._timeout = timeout
        self.key = "http_" + provider

    def propose(self, context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        task = str(context.get("task") or "").strip()
        if task == "search_queries":
            system = _SEARCH_QUERY_PROMPT
            timeout = self._timeout
            payload = {
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _search_query_prompt(context)},
                ],
            }
            content = _message_content(_post_chat(self, payload, timeout))
            return [
                {"kind": "query", "text": item}
                for item in parse_search_queries(content)
            ]
        if task == "round_questions":
            system = _ROUND_QUESTION_PROMPT
            timeout = self._timeout
            payload = {
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _round_question_prompt(context)},
                ],
            }
            content = _message_content(_post_chat(self, payload, timeout))
            return parse_round_questions(content)
        if task == "round_decision":
            payload = {
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": _ROUND_DECISION_PROMPT},
                    {"role": "user", "content": _round_question_prompt(context)},
                ],
            }
            content = _message_content(_post_chat(self, payload, self._timeout))
            return parse_round_decision(content)
        if task == "snapshot_excerpts":
            system = _SNAPSHOT_EXCERPT_PROMPT
            timeout = max(self._timeout, 60)
            payload = {
                "model": self._model,
                "temperature": 0,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": _snapshot_excerpt_prompt(context)},
                ],
            }
            content = _message_content(_post_chat(self, payload, timeout))
            return parse_snapshot_excerpts(content)
        if task == "revision":
            system = _REVISION_PROMPT
            allowed = {"revision"}
            timeout = max(self._timeout, 60)
        else:
            system = _SYSTEM_PROMPT
            allowed = {"finding", "option"}
            timeout = self._timeout
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": _user_prompt(context)},
            ],
        }
        content = _message_content(_post_chat(self, payload, timeout))
        return parse_draft_proposals(content, allowed_kinds=allowed)


def parse_round_questions(content: str) -> list[dict[str, str]]:
    data = _load_json_payload(content)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("questions") or data.get("proposals")
    else:
        items = None
    if not isinstance(items, list):
        raise DraftSuggestionError("模型没有给出本轮问题。")
    questions: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            question = item.strip()
            why = ""
            label = ""
            section = ""
        elif isinstance(item, dict):
            question = str(item.get("question") or item.get("text") or "").strip()
            why = str(item.get("enough_for_now") or "").strip()
            label = str(item.get("label") or "").strip()
            section = str(item.get("section") or "").strip()
        else:
            continue
        question = " ".join(question.split())
        if not question or question in seen:
            continue
        seen.add(question)
        row = {"kind": "question", "question": question, "text": question}
        if why:
            row["enough_for_now"] = why
        if label:
            row["label"] = " ".join(label.split())
        if section:
            row["section"] = " ".join(section.split())
        questions.append(row)
        if len(questions) >= 5:
            break
    if not questions:
        raise DraftSuggestionError("模型没有给出本轮问题。")
    return questions


def parse_round_decision(content: str) -> list[dict[str, str]]:
    data = _load_json_payload(content)
    text = ""
    if isinstance(data, str):
        text = data
    elif isinstance(data, dict):
        text = str(data.get("decision") or data.get("text") or "")
    text = " ".join(text.split())
    if not text:
        raise DraftSuggestionError("模型没有给出这一轮要决定什么。")
    return [{"kind": "decision", "text": text[:120]}]


def parse_snapshot_excerpts(content: str) -> list[dict[str, str]]:
    data = _load_json_payload(content)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("excerpts") or data.get("proposals")
    else:
        items = None
    if not isinstance(items, list):
        raise DraftSuggestionError("模型没有从快照摘下原话。")
    excerpts: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = str(item.get("text") or item.get("excerpt") or "").strip()
        else:
            continue
        text = " ".join(text.split())
        if not text or text in seen:
            continue
        seen.add(text)
        excerpts.append({"kind": "excerpt", "text": text})
        if len(excerpts) >= 5:
            break
    if not excerpts:
        raise DraftSuggestionError("模型没有从快照摘下原话。")
    return excerpts


def _snapshot_excerpt_prompt(context: Mapping[str, Any]) -> str:
    lines = []
    decision = str(context.get("decision_question") or "").strip()
    if decision:
        lines.append("本轮要决定：" + decision)
    focus = str(context.get("focus_question") or "").strip()
    if focus:
        lines.append("先回答这一条：" + focus)
    title = str(context.get("source_title") or "").strip()
    if title:
        lines.append("材料名：" + title)
    block = str(context.get("block_title") or "").strip()
    if block:
        lines.append("要挂到这一节：" + block)
    snapshot = str(context.get("snapshot_text") or "").strip()
    if snapshot:
        lines.append("快照正文：")
        lines.append(snapshot)
    else:
        lines.append("快照正文是空的。")
    return "\n".join(lines)


def _round_question_prompt(context: Mapping[str, Any]) -> str:
    lines = []
    decision = str(context.get("decision_question") or "").strip()
    if decision:
        lines.append("本轮要决定：" + decision)
    original = str(context.get("original_context") or "").strip()
    if original and original != decision:
        lines.append("经理原话：" + original)
    existing = context.get("questions") or []
    if existing:
        lines.append("已有本轮问题（可能是别的模板带入，与原话无关则不要沿用）：")
        for item in existing:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    archived = context.get("archived_questions") or []
    if archived:
        lines.append("上一轮已经问过（不要原样重复）：")
        for item in archived:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    sections = context.get("previous_sections") or []
    if sections:
        lines.append("上一轮给经理的稿（审阅它：哪条问题答到什么程度、还差什么）：")
        for item in sections:
            title = str((item or {}).get("title") or "").strip()
            body = str((item or {}).get("text") or "").strip()
            if not body:
                continue
            if len(body) > 1200:
                body = body[:1200] + "…（后略）"
            lines.append("【" + title + "】" + body)
    feedback = context.get("manager_feedback") or []
    if feedback:
        lines.append("经理这一轮的反馈（内部指示，不是证据，但这一轮要围着它转）：")
        for item in feedback:
            note = str(item or "").strip()
            if not note:
                continue
            if len(note) > 2000:
                note = note[:2000] + "…（后略）"
            lines.append("- " + note)
    sections = context.get("sections") or []
    if sections:
        lines.append("稿现在的节（section 要从这些里挑，原样照抄；都放不下才写新节名）：")
        for item in sections:
            name = str(item or "").strip()
            if name:
                lines.append("- " + name)
    hints = context.get("template_hints") or []
    if hints:
        lines.append("参考标签（仅当原话明显同类才可借鉴）：")
        for item in hints:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    if not lines:
        lines.append("还没有经理原话。")
    return "\n".join(lines)


def parse_search_queries(content: str) -> list[str]:
    data = _load_json_payload(content)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("queries")
    else:
        items = None
    if not isinstance(items, list):
        raise DraftSuggestionError("模型没有给出检索词。")
    queries: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip() if not isinstance(item, dict) else str(item.get("text") or "").strip()
        lowered = text.lower()
        if not text or "http://" in lowered or "https://" in lowered or "www." in lowered:
            continue
        text = " ".join(text.split())
        if text in seen:
            continue
        queries.append(text)
        seen.add(text)
        if len(queries) >= 3:
            break
    if not queries:
        raise DraftSuggestionError("模型没有给出检索词。")
    return queries


def _search_query_prompt(context: Mapping[str, Any]) -> str:
    lines = []
    decision = str(context.get("decision_question") or "").strip()
    if decision:
        lines.append("本轮要决定：" + decision)
    original = str(context.get("original_context") or "").strip()
    if original and original != decision:
        lines.append("经理原话：" + original)
    focus = str(context.get("focus_question") or "").strip()
    if focus:
        lines.append("先搜这一条：" + focus)
    questions = context.get("questions") or []
    if questions:
        lines.append("本轮问题：")
        for item in questions:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    if not lines:
        lines.append("本轮问题还没写。")
    return "\n".join(lines)


def _post_chat(adapter: HttpJsonDraftAdapter, payload: Mapping[str, Any], timeout: int) -> dict[str, Any]:
    request = Request(
        adapter._url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + adapter._api_key,
            "Content-Type": "application/json",
        },
    )
    try:
        with adapter._opener(request, timeout=timeout) as response:
            raw = response.read(256 * 1024 + 1)
    except HTTPError as error:
        raise DraftSuggestionError(
            "模型没连上，没有改内部稿。请检查本机密钥或稍后再试。"
        ) from error
    except URLError as error:
        raise DraftSuggestionError(
            "模型没连上，没有改内部稿。请检查本机网络或稍后再试。"
        ) from error
    except TimeoutError as error:
        raise DraftSuggestionError(
            "模型先拟超时，没有改内部稿。"
        ) from error
    if len(raw) > 256 * 1024:
        raise DraftSuggestionError("模型返回过长，没有写入候选，也没有改内部稿。")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DraftSuggestionError("模型返回无法阅读，没有改内部稿。") from error


def parse_draft_proposals(
    content: str,
    *,
    allowed_kinds: set[str] | None = None,
) -> list[dict[str, str]]:
    allowed = allowed_kinds or {"finding", "option"}
    limit = MAX_REVISION_CHARS if allowed == {"revision"} else MAX_PROPOSAL_CHARS
    empty_error = (
        "模型没有给出可先拟的改稿。"
        if allowed == {"revision"}
        else "模型没有给出可先拟的候选。"
    )
    data = _load_json_payload(content)
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("proposals")
        if items is None:
            raise DraftSuggestionError(empty_error)
    else:
        raise DraftSuggestionError(empty_error)
    if not isinstance(items, list):
        raise DraftSuggestionError(empty_error)
    chosen: dict[str, str] = {}
    order: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        text = str(item.get("text") or "").strip()
        if kind not in allowed or not text:
            continue
        if len(text) > limit:
            text = text[:limit].rstrip()
        if kind in chosen:
            continue
        chosen[kind] = text
        order.append(kind)
    proposals = [{"kind": kind, "text": chosen[kind]} for kind in order]
    if not proposals:
        raise DraftSuggestionError(empty_error)
    return proposals


def resolve_draft_adapter(
    environ: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> UnconfiguredDraftAdapter | HttpJsonDraftAdapter:
    env = environ if environ is not None else os.environ
    api_key = str(env.get("JINGWEI_DRAFT_API_KEY") or "").strip()
    if not api_key:
        return UnconfiguredDraftAdapter()
    provider, url, model = resolve_draft_endpoint(env)
    return HttpJsonDraftAdapter(
        url=url,
        api_key=api_key,
        model=model,
        opener=opener,
        provider=provider,
    )


def resolve_draft_endpoint(environ: Mapping[str, str]) -> tuple[str, str, str]:
    raw = str(environ.get("JINGWEI_DRAFT_PROVIDER") or DEFAULT_DRAFT_PROVIDER).strip().lower()
    provider = _PROVIDER_ALIASES.get(raw, raw) or DEFAULT_DRAFT_PROVIDER
    override_url = str(environ.get("JINGWEI_DRAFT_URL") or "").strip()
    override_model = str(environ.get("JINGWEI_DRAFT_MODEL") or "").strip()
    if provider == "custom":
        if not override_url:
            raise DraftSuggestionError(
                "自定义模型需要同时设置 JINGWEI_DRAFT_URL。没有改内部稿。"
            )
        model = override_model or DEFAULT_DRAFT_MODEL
        return provider, override_url, model
    preset = DRAFT_PROVIDERS.get(provider)
    if preset is None:
        names = "、".join(sorted(DRAFT_PROVIDERS) + ["custom"])
        raise DraftSuggestionError(
            "还不认识这个模型服务。可用 " + names + "，或设 JINGWEI_DRAFT_URL。没有改内部稿。"
        )
    url = override_url or preset["url"]
    model = override_model or preset["model"]
    return provider, url, model


def _user_prompt(context: Mapping[str, Any]) -> str:
    title = str(context.get("title") or "未命名段落").strip()
    claims = context.get("claims") or []
    excerpts = context.get("excerpts") or []
    questions = context.get("questions") or []
    lines = ["段落标题：" + title]
    decision = str(context.get("decision_question") or "").strip()
    if decision:
        lines.append("本轮要决定：" + decision)
    original = str(context.get("original_context") or "").strip()
    if original and original != decision:
        lines.append("经理原话：" + original)
    focus = str(context.get("focus_question") or "").strip()
    if focus:
        lines.append("这一节先回答：" + focus)
    enough = str(context.get("enough_for_now") or "").strip()
    if enough:
        lines.append("这条何时够用：" + enough)
    if questions:
        lines.append("本轮问题：")
        for item in questions:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    materials = context.get("materials") or []
    if materials:
        lines.append("材料匣：")
        for item in materials:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    else:
        lines.append("材料匣：（还没有材料）")
    current = str(context.get("current_text") or "").strip()
    if context.get("placeholder"):
        lines.append(
            "现稿：（这一节还是空的。写缺口稿，不要复述占位句。）"
        )
    elif current:
        lines.extend(["现稿：", current])
    lines.append("这一节已挂原话：")
    hung_lines = context.get("excerpt_lines") or excerpts
    if hung_lines:
        for item in hung_lines:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    else:
        lines.append("（没有）")
    other_lines = context.get("other_excerpt_lines") or (
        context.get("other_excerpts") or []
    )
    if other_lines:
        lines.append("材料匣其他原话（未挂到这一节）：")
        for item in other_lines:
            text = str(item or "").strip()
            if text:
                lines.append("- " + text)
    lines.append("已挂依据：")
    if not claims:
        lines.append("（没有已挂依据）")
    else:
        for item in claims:
            text = str(item.get("text") or "").strip()
            if text:
                lines.append("- " + text)
    return "\n".join(lines)


def _message_content(body: Mapping[str, Any]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise DraftSuggestionError("模型没有给出可先拟的候选。")
    first = choices[0]
    if not isinstance(first, dict):
        raise DraftSuggestionError("模型没有给出可先拟的候选。")
    message = first.get("message")
    if not isinstance(message, dict):
        raise DraftSuggestionError("模型没有给出可先拟的候选。")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise DraftSuggestionError("模型没有给出可先拟的候选。")
    return content


def _load_json_payload(content: str) -> Any:
    text = content.strip()
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise DraftSuggestionError("模型返回无法阅读，没有改内部稿。")
