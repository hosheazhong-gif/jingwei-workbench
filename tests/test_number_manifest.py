from __future__ import annotations

import unittest

from app.projections.numbers import number_manifest

EXCERPTS = [
    {
        "excerpt": (
            "公司自建供应链较完善，生产环节截至1Q22末已建立252亩智能制造产业园，"
            "从产销数据推算，核心原料中固体饮料已实现100%自供，风味饮料浓浆、果酱"
            "分别约89%、30%；物流仓储环节已在22省设立基地。"
        ),
        "source_title": "某研报",
        "source_id": "S-022",
    },
    {
        "excerpt": "公司19~21收入CAGR101%，2021年净利19.1亿元、净利率近19%。",
        "source_title": "某研报",
        "source_id": "S-022",
    },
]


class NumberManifestTest(unittest.TestCase):
    def test_every_number_traces_back_when_the_draft_is_honest(self) -> None:
        text = (
            "一、供应链\n"
            "1. 据某研报，已建立252亩智能制造产业园。\n"
            "2. 固体饮料已实现100%自供，风味饮料浓浆约89%，果酱约30%。\n"
            "3. 物流已在22个省设立基地。\n"
        )
        manifest = number_manifest(text, EXCERPTS)
        self.assertEqual(manifest["unsourced"], 0)
        self.assertTrue(manifest["total"] >= 5)
        for item in manifest["numbers"]:
            self.assertTrue(item["found_in_excerpt"], item["number"])
            self.assertEqual(item["source_id"], "S-022")

    def test_a_number_the_model_made_up_is_caught(self) -> None:
        # 这是这张表存在的全部理由：一个编出来的数字混在通顺的句子里，
        # 人读一遍不会怀疑，但它在已挂原话里找不到。
        text = "1. 物流已在22个省设立基地，覆盖全国约 3.2 万家门店。\n"
        manifest = number_manifest(text, EXCERPTS)
        missing = [x for x in manifest["numbers"] if not x["found_in_excerpt"]]
        self.assertEqual([x["number"] for x in missing], ["3.2"])
        self.assertEqual(manifest["unsourced"], 1)
        # 要能说清是哪一句里的那个数字，光报个数帮不上忙
        self.assertIn("门店", missing[0]["context"])

    def test_year_and_list_markers_are_not_data(self) -> None:
        text = "一、政策\n1. 2021年发布该文件。\n2. 2022年修订过一次。\n"
        manifest = number_manifest(text, EXCERPTS)
        self.assertEqual(manifest["numbers"], [])

    def test_it_only_says_whether_the_number_appears_not_whether_it_is_right(self) -> None:
        # 说清能力边界：挂上了也可能是材料本身写错的
        manifest = number_manifest("1. 自供比例约89%。\n", EXCERPTS)
        self.assertTrue(manifest["numbers"][0]["found_in_excerpt"])
        self.assertIn("不是", manifest["limitation"])
        self.assertIn("材料本身写错", manifest["limitation"])

    def test_no_excerpts_means_every_number_is_unsourced(self) -> None:
        manifest = number_manifest("1. 净利19.1亿元。\n", [])
        self.assertEqual(manifest["unsourced"], 1)
        self.assertFalse(manifest["numbers"][0]["found_in_excerpt"])
        self.assertIsNone(manifest["numbers"][0]["source_id"])


if __name__ == "__main__":
    unittest.main()
