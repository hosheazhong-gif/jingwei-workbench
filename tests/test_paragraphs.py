from __future__ import annotations

import unittest

from app.projections.paragraphs import ensure_paragraphs, paragraph_spans


class ParagraphsTest(unittest.TestCase):
    def test_ensure_paragraphs_keeps_existing_breaks(self) -> None:
        source = "1. 先补口径。\n2. 再补租户。"
        self.assertEqual(ensure_paragraphs(source), source)

    def test_ensure_paragraphs_splits_sentence_wall(self) -> None:
        source = "第一句。第二句。第三句。第四句。"
        self.assertEqual(
            ensure_paragraphs(source),
            "第一句。\n\n第二句。\n\n第三句。\n\n第四句。",
        )

    def test_ensure_paragraphs_leaves_one_sentence(self) -> None:
        source = "只有一句，还不用拆。"
        self.assertEqual(ensure_paragraphs(source), source)

    def test_ensure_paragraphs_groups_long_walls(self) -> None:
        source = "一。二。三。四。五。"
        self.assertEqual(ensure_paragraphs(source), "一。二。\n\n三。四。\n\n五。")

    def test_paragraph_spans_split_newlines_without_changing_words(self) -> None:
        source = "客户资料请求：\n1. 租户结构。\n2. 冷链仓容。"
        spans = paragraph_spans(source)
        self.assertEqual(
            [piece for piece, _ in spans],
            ["客户资料请求：", "1. 租户结构。", "2. 冷链仓容。"],
        )
        for piece, start in spans:
            self.assertEqual(source[start : start + len(piece)], piece)
