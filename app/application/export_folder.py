"""把一份导出顺手存到题目文件夹。

受控副本（`var/<db>-files/…`）是账本的一部分，人不该去动它；导出是交给人的
东西，人会整理、会重命名、会发出去。两者必须分开放——否则人整理文件时顺手
删掉一个快照，那条原话就失去了受控副本，追溯链断在那儿，而且没人会收到提示。
理由和落地形状见 PRD 第 20.9 节。

导出永远不覆盖已经存在的文件：上一版可能已经发出去了。
"""

from __future__ import annotations

import base64
import re
from pathlib import Path
from typing import Any

_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_MAX_NAME_CHARS = 80

# 文件名里写「哪一份」比再抄一遍题目名有用
EXPORT_LABELS = {
    "word": "整理稿",
    "markdown": "整理稿",
    "word_detailed": "详细版",
    "markdown_detailed": "详细版",
    "plain_text": "纯文本",
}


class ExportFolderError(ValueError):
    pass


def safe_folder_name(name: Any, fallback: str) -> str:
    """题目名变成能当文件夹名的东西；变不出来就退回题目 ID。"""
    text = _ILLEGAL.sub("", str(name or ""))
    text = " ".join(text.split())
    # Windows 上结尾的点和空格会被悄悄吃掉，先剥干净
    text = text.strip(" .")
    if len(text) > _MAX_NAME_CHARS:
        text = text[:_MAX_NAME_CHARS].rstrip(" .")
    return text or fallback


def save_export_to_folder(
    export: dict[str, Any],
    *,
    exports_root: Path,
    project_name: Any,
    project_id: str,
    stamp: str,
    label: str = "",
) -> Path:
    """写到 <exports_root>/<题目名>/<日期> <整理稿|详细版>.<扩展名>。

    文件夹已经是题目名了，文件名里不必再重复一遍，写清是哪一份更有用。
    重名不覆盖，加 (2) (3)。
    """
    folder = exports_root / safe_folder_name(project_name, project_id)
    folder.mkdir(parents=True, exist_ok=True)
    resolved_root = exports_root.resolve()
    if resolved_root not in folder.resolve().parents and folder.resolve() != resolved_root:
        raise ExportFolderError("导出目录不得跑到题目文件夹外面")

    filename = safe_folder_name(export.get("filename"), "导出.md")
    stem, dot, suffix = filename.rpartition(".")
    if not dot:
        stem, suffix = filename, ""
    stem = safe_folder_name(label, "") or stem
    target = _free_path(folder, f"{stamp} {stem}", suffix)

    if export.get("content_encoding") == "base64":
        target.write_bytes(base64.b64decode(export["content"]))
    else:
        target.write_text(str(export.get("content") or ""), encoding="utf-8")
    return target


def _free_path(folder: Path, stem: str, suffix: str) -> Path:
    tail = f".{suffix}" if suffix else ""
    candidate = folder / f"{stem}{tail}"
    index = 2
    while candidate.exists():
        candidate = folder / f"{stem} ({index}){tail}"
        index += 1
        if index > 999:
            raise ExportFolderError("同名文件太多，先清一清这个文件夹")
    return candidate
