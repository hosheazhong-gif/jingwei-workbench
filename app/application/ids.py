from __future__ import annotations

from typing import Any


def allocate_prefixed_id(connection: Any, table: str, prefix: str) -> str:
    used = {row["id"] for row in connection.execute(f"SELECT id FROM {table}")}
    index = 1
    while f"{prefix}-{index:03d}" in used:
        index += 1
    return f"{prefix}-{index:03d}"
