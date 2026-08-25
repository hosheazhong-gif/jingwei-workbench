from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class ModelDraftAdapter(Protocol):
    key: str

    def propose(self, context: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]: ...
