from __future__ import annotations

import re

_BREAK = re.compile(r"[。！？]")


def paragraph_spans(text: str) -> list[tuple[str, int]]:
    """按已有换行或每两句切段，返回 (段文, 原文起点)。不改用词。"""
    raw = (text or "").replace("\r\n", "\n")
    if not raw.strip():
        return []
    if "\n" in raw:
        rows: list[tuple[str, int]] = []
        start = 0
        for index, character in enumerate(raw):
            if character != "\n":
                continue
            piece = raw[start:index]
            if piece.strip():
                rows.append((piece, start))
            start = index + 1
        tail = raw[start:]
        if tail.strip():
            rows.append((tail, start))
        return rows
    parts: list[tuple[str, int]] = []
    last = 0
    for match in _BREAK.finditer(raw):
        end = match.end()
        piece = raw[last:end]
        if piece.strip():
            parts.append((piece, last))
        last = end
    tail = raw[last:]
    if tail.strip():
        parts.append((tail, last))
    if len(parts) <= 1:
        return [(raw.strip(), 0)]
    if len(parts) <= 4:
        return parts
    grouped: list[tuple[str, int]] = []
    for index in range(0, len(parts), 2):
        chunk = parts[index : index + 2]
        grouped.append(("".join(item[0] for item in chunk), chunk[0][1]))
    return grouped


def ensure_paragraphs(text: str) -> str:
    """新写候选时把一整段墙拆成短段；已有换行则原样留下。"""
    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw or "\n" in raw:
        return raw
    spans = paragraph_spans(raw)
    if len(spans) <= 1:
        return raw
    return "\n\n".join(piece.strip() for piece, _ in spans)
