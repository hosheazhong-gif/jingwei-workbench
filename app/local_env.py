from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path


def load_local_env(project_root: Path) -> None:
    """Load KEY=VALUE lines from ``.env``. Do not override variables already set."""
    path = project_root / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def update_local_env(
    project_root: Path, updates: Mapping[str, str | None]
) -> Path:
    """Atomically update selected ``.env`` keys while preserving unrelated lines."""
    project_root.mkdir(parents=True, exist_ok=True)
    path = project_root / ".env"
    managed = set(updates)
    kept: list[str] = []
    if path.is_file():
        for raw in path.read_text(encoding="utf-8").splitlines():
            stripped = raw.strip()
            if "=" in stripped and not stripped.startswith("#"):
                key = stripped.split("=", 1)[0].strip()
                if key in managed:
                    continue
            kept.append(raw)

    values: list[str] = []
    for key, raw_value in updates.items():
        value = None if raw_value is None else str(raw_value).strip()
        if value is None or value == "":
            continue
        if "\n" in value or "\r" in value:
            raise ValueError("设置值不能包含换行")
        values.append(f"{key}={value}")

    lines = kept
    if values:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend(values)
    content = "\n".join(lines).rstrip() + ("\n" if lines else "")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=".env.",
            suffix=".tmp",
            dir=project_root,
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
        temporary_name = None
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if temporary_name:
            try:
                Path(temporary_name).unlink()
            except FileNotFoundError:
                pass
    return path
