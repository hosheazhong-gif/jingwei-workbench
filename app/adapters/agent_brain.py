"""把已配置的模型端点包成研究代理要的 `decide(prompt) -> str`。

**代理不是第二个模型接入点。**同一个端点、同一份密钥、同一套错误措辞，
全部复用 `http_draft.py` 里已经跑通的那条链路——用户在「连接模型」里填一次就够了。

放这儿是为了让你先看清楚它有多薄（就一个方法）。**真要合进主线，建议直接搬进
`app/adapters/http_draft.py`**：`_post_chat` 和 `_message_content` 就在那个文件里，
搬过去就不用跨模块引私有函数了。

顺带说明：读 `adapter._model` / `adapter._timeout` 这种写法不是我起的头——
`http_draft.test_draft_connection` 本来就这么写。保持一致，不另立风格。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.adapters.http_draft import (  # noqa: PLC2701
    HttpJsonDraftAdapter,
    _message_content,
    _post_chat,
    resolve_draft_adapter,
)
from app.adapters.unconfigured_draft import UnconfiguredDraftAdapter

# 代理的系统提示只说边界，具体任务在每轮的 user prompt 里。
_AGENT_SYSTEM = """你是检索代理，只负责决定下一轮搜什么词。
你不写稿、不下结论、不判断哪条材料算数——那些都是人的事，你越界会被拒绝。
每轮只输出一个 JSON 对象，不要任何解释、不要代码块围栏。"""


class HttpAgentBrain:
    """OpenAI 兼容 chat completions，返回原始文本，解析交给调用方。"""

    key = "http_agent"

    def __init__(self, adapter: HttpJsonDraftAdapter, *, timeout: int | None = None) -> None:
        self._adapter = adapter
        self._timeout = timeout or adapter._timeout
        self.key = "agent_" + adapter.key.removeprefix("http_")

    def decide(self, prompt: str) -> str:
        payload: Mapping[str, Any] = {
            "model": self._adapter._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _AGENT_SYSTEM},
                {"role": "user", "content": prompt},
            ],
        }
        return _message_content(_post_chat(self._adapter, payload, self._timeout))


def resolve_agent_brain(
    environ: Mapping[str, str] | None = None,
    opener: Any = None,
) -> HttpAgentBrain | None:
    """没配密钥就返回 None，让上层给出「还没接模型」那句话，而不是在这里编造。"""
    adapter = resolve_draft_adapter(environ=environ, opener=opener)
    if isinstance(adapter, UnconfiguredDraftAdapter):
        return None
    return HttpAgentBrain(adapter)
