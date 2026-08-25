"""认出粘进来的材料本来的结构，只排版，不改字。

材料是人整段复制进来的，里面常常自带层级：小标题、一二三四的分条、表3-1 这样的
图表题，以及用制表符分列的表格。之前导出把这些全当成普通段落倒进去，于是一份
本来有条理的政策汇总，进了 Word 就是一面墙。

这里只做一件事：判断每一行是什么，交给导出器选对应的样式。判断不准就按普通段落
处理——宁可少认，也不能替材料改写内容。一个字都不动，也不合并、不删除任何行。
"""

from __future__ import annotations

import re
from typing import Any

# 表3-1 / 图 2 / 表 3-1： 这一类图表题
_CAPTION = re.compile(r"^[（(]?\s*(表|图|附表|附图)\s*[0-9０-９]+([-–—.·][0-9０-９]+)?\s*[）)]?\s*[^\n]{0,80}$")
# 一、 / （一） / 1、 / 1. / (1) / ① 这一类条目开头
_ITEM = re.compile(
    r"^\s*("
    r"[一二三四五六七八九十百]+\s*[、．.]"
    r"|[（(]\s*[一二三四五六七八九十百]+\s*[）)]"
    r"|[0-9０-９]{1,2}\s*[、．.]"
    r"|[（(]\s*[0-9０-９]{1,2}\s*[）)]"
    r"|[①-⑳]"
    r"|[-–—•·]\s"
    r")"
)
_ENDS = "。！？；：…”』」》.!?;:,，、"
_HEADING_MAX = 26


def structure_lines(text: str) -> list[dict[str, Any]]:
    """把一段材料原文拆成有类型的块。

    返回 [{"kind": "para"|"heading"|"item"|"caption"|"table", ...}]。
    kind=table 的块带 "rows"，其余带 "text"。
    """
    raw = str(text or "").replace("\r\n", "\n")
    lines = [line.rstrip() for line in raw.split("\n")]
    blocks: list[dict[str, Any]] = []
    pending: list[list[str]] = []

    def flush_table() -> None:
        if not pending:
            return
        # 一行制表符不算表格；至少两行、至少两列才还原成表
        if len(pending) >= 2:
            blocks.append({"kind": "table", "rows": [list(row) for row in pending]})
        else:
            for row in pending:
                blocks.append({"kind": "para", "text": "\t".join(row)})
        pending.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_table()
            continue
        cells = [cell.strip() for cell in line.split("\t")]
        if len(cells) >= 2 and sum(1 for cell in cells if cell) >= 2:
            pending.append(cells)
            continue
        flush_table()
        blocks.append({"kind": _kind_of(stripped), "text": stripped})
    flush_table()
    return blocks


def line_kind(line: str) -> str:
    """单独判断一行的类型；正文要保留标红位置时用这个，不做表格还原。"""
    return _kind_of(line.strip())


def _kind_of(line: str) -> str:
    if _CAPTION.match(line):
        return "caption"
    if _ITEM.match(line):
        return "item"
    if _looks_like_heading(line):
        return "heading"
    return "para"


def _looks_like_heading(line: str) -> bool:
    """短、不以句读收尾、没有句号，才算材料自己的小标题。

    宁可少认。认错一行只会让它多一个加粗，认多了会把正文切碎。
    """
    if len(line) > _HEADING_MAX:
        return False
    if line[-1] in _ENDS:
        return False
    if "。" in line or "；" in line:
        return False
    # 一句话里带了完整的主谓宾还是可能超短，这里再要求它不含逗号
    return "，" not in line and "," not in line


def table_widths(rows: list[list[str]]) -> int:
    return max((len(row) for row in rows), default=0)
