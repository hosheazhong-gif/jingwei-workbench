from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.application.draft_suggestion import DraftSuggestionError


class UnconfiguredDraftAdapter:
    """未接模型时拒绝编造，也不写成内部稿。"""

    key = "unconfigured"

    def propose(self, context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        raise DraftSuggestionError(
            "还没接模型。本机设置 JINGWEI_DRAFT_API_KEY，并用 JINGWEI_DRAFT_PROVIDER=deepseek 等选定服务后，「请模型先拟」才会出候选。"
            "拆本轮问题、改这段、总判断和方向现在仍可人写，也不会自动写成内部稿。"
        )
