from __future__ import annotations

import unittest

from app.projections.checks import mechanical_checks, repeated_phrases

# 2026-08-23 实测：P-005 那节收下的稿，13 条正文里 9 条在说「材料没说什么」，
# 小结又把正文的数字整串抄了一遍。缺口本身有价值（尤其尽调），问题是散着说、
# 反复说。这两条检查只提示不拦截。
NOISY = """一、供应链体系
（一）生产环节的自产比例
1. 据某研报，公司自建供应链较完善，已建立252亩智能制造产业园。
2. 该报告称在四川安岳就近建厂，但未说明该合作的具体模式、采购量或股权关系。
3. 报告推算固体饮料已实现100%自供，风味饮料浓浆约89%，果酱约30%。
4. 上述比例系报告推算，并非官方披露，且未说明推算方法、口径或数据来源。
5. 报告未披露其他核心原料的自产比例，也未说明各原料权重，因此无法计算整体自产率。
（二）物流仓储环节
1. 同一报告称已在22个省设立基地，并给予加盟商全国免运费政策。
2. 报告未说明这22个省级基地的分布、面积或配送时效，也未说明免运费的适用范围。
3. 报告未披露自营与外包比例，也未提供物流成本占比，因此无法评估效率贡献。
二、小结
本节显示公司自建供应链较完善，固体饮料自供比例推算为100%，风味饮料浓浆约89%，
果酱约30%；但毛利率具体数值未披露。下一步需查招股书原文。
"""

CLEAN = """一、政策口径
1. 据某文件，本地对该产业有专项补贴。
2. 据同一文件，申报窗口为每年三月。
3. 这一小节还缺：补贴额度和申报门槛，文件里都没有写。
二、小结
这一节答到了政策适用范围这一步。还缺申报的具体门槛。下一步查实施细则。
"""


class RepeatedPhrasesTest(unittest.TestCase):
    def test_gaps_scattered_across_every_item_is_flagged(self) -> None:
        kinds = [item["kind"] for item in repeated_phrases(NOISY)]
        self.assertIn("gap_spread", kinds)

    def test_summary_that_copies_the_body_is_flagged(self) -> None:
        echo = [x for x in repeated_phrases(NOISY) if x["kind"] == "summary_echo"]
        self.assertTrue(echo)
        # 要能说出抄的是哪几处，光说「有重复」帮不上忙
        self.assertTrue(echo[0]["samples"])
        self.assertTrue(echo[0]["hint"])

    def test_a_tidy_section_is_not_flagged(self) -> None:
        # 缺口集中成一条、小结不复述数字——这样写不该被提示
        self.assertEqual(repeated_phrases(CLEAN), [])

    def test_check_rides_along_with_the_other_mechanical_checks(self) -> None:
        checks = mechanical_checks(NOISY, [], set())
        self.assertIn("repeated_phrases", checks)
        self.assertTrue(checks["repeated_phrases"])
        # 只提示，不改变别的检查，也不拦截收下
        self.assertFalse(checks["stale"])
        self.assertEqual(checks["client_as_verified"], [])

    def test_empty_text_is_quiet(self) -> None:
        self.assertEqual(repeated_phrases(""), [])
        self.assertEqual(repeated_phrases("只有一句话。"), [])


if __name__ == "__main__":
    unittest.main()
