from __future__ import annotations

import unittest

from app.projections.impact import cross_section_impact


def block(
    block_id: str,
    title: str,
    text: str,
    *,
    claims: list[tuple[str, str, str, str]] | None = None,
    revision: str | None = None,
) -> dict:
    return {
        "id": block_id,
        "title": title,
        "current_text": text,
        "claim_sources": [
            {
                "claim_id": claim_id,
                "claim_text": claim_text,
                "source_id": source_id,
                "source_title": source_title,
            }
            for claim_id, claim_text, source_id, source_title in (claims or [])
        ],
        "pending_revision": {"body": revision} if revision is not None else None,
    }


class CrossSectionImpactTest(unittest.TestCase):
    """改一节，别的哪几节跟着要看一眼。只沿已经挂着的关系算，不猜。"""

    def test_changed_number_still_written_elsewhere_is_flagged(self) -> None:
        blocks = [
            block(
                "B1",
                "供应链与成本优势",
                "2024 年营收 19.1亿元。",
                revision="2024 年营收 20.3亿元。",
            ),
            block("B2", "加盟收入和单店模型", "公司营收 19.1亿元，门店扩张很快。"),
        ]
        result = cross_section_impact(blocks)
        related = result["B1"]["related"]
        self.assertEqual([row["title"] for row in related], ["加盟收入和单店模型"])
        self.assertEqual(related[0]["reasons"][0]["kind"], "changed_number")
        self.assertIn("19.1亿", related[0]["reasons"][0]["detail"])
        self.assertIn("还照旧写着老数", result["B1"]["heading"])

    def test_no_pending_revision_means_no_changed_number(self) -> None:
        blocks = [
            block("B1", "一节", "营收 19.1亿元。"),
            block("B2", "二节", "营收 19.1亿元。"),
        ]
        result = cross_section_impact(blocks)
        self.assertEqual(result["B1"]["changed_numbers"], [])
        self.assertEqual(result["B1"]["related"][0]["reasons"][0]["kind"], "same_number")

    def test_shared_claim_and_source_are_listed(self) -> None:
        claim = ("C-1", "行业集中度在提高", "S-1", "招股书")
        blocks = [
            block("B1", "一节", "没有数字。", claims=[claim]),
            block("B2", "二节", "也没有数字。", claims=[claim]),
        ]
        result = cross_section_impact(blocks)
        kinds = [item["kind"] for item in result["B1"]["related"][0]["reasons"]]
        self.assertEqual(kinds[0], "same_claim")
        self.assertIn("same_source", kinds)

    def test_round_percentages_and_small_integers_are_not_a_relation(self) -> None:
        """真机上 70% 把每一节都连到了每一节。哪儿都亮的提示等于没提示。"""
        blocks = [
            block("B1", "一节", "国产化率约 70%，一共 3 家。"),
            block("B2", "二节", "政策目标是 70%，涉及 3 个方向。"),
        ]
        result = cross_section_impact(blocks)
        self.assertEqual(result["B1"]["related"], [])
        self.assertIn("没有别的小节", result["B1"]["heading"])

    def test_magnitude_and_precise_numbers_do_count(self) -> None:
        blocks = [
            block("B1", "一节", "补贴 1.3亿元，覆盖 4,184.4 家门店。"),
            block("B2", "二节", "同一笔 1.3亿元的补贴。"),
            block("B3", "三节", "门店 4,184.4 家。"),
        ]
        result = cross_section_impact(blocks)
        self.assertEqual(
            sorted(row["title"] for row in result["B1"]["related"]), ["三节", "二节"]
        )

    def test_years_are_not_a_relation(self) -> None:
        blocks = [
            block("B1", "一节", "2024 年开工。"),
            block("B2", "二节", "2024 年投产。"),
        ]
        self.assertEqual(cross_section_impact(blocks)["B1"]["related"], [])

    def test_it_never_rewrites_and_says_so(self) -> None:
        blocks = [
            block("B1", "一节", "营收 19.1亿元。", revision="营收 20.3亿元。"),
            block("B2", "二节", "营收 19.1亿元。"),
        ]
        before = [item["current_text"] for item in blocks]
        result = cross_section_impact(blocks)
        self.assertEqual([item["current_text"] for item in blocks], before)
        self.assertIn("不会自动去改它们", result["B1"]["limitation"])

    def test_a_lone_section_has_nothing_to_report(self) -> None:
        result = cross_section_impact([block("B1", "一节", "营收 19.1亿元。")])
        self.assertEqual(result["B1"]["related"], [])


if __name__ == "__main__":
    unittest.main()
