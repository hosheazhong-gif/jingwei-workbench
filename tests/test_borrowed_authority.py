from __future__ import annotations

import unittest

from app.projections.checks import unsourced_orgs


class BorrowedAuthorityTest(unittest.TestCase):
    """「某某机构预计」这种借来的权威，挂不上原话就得标出来。

    现场缺陷（docs/20 §6，2026-08-26）：真材料走查时往五份稿里各塞一条编造的
    机构归属，数字全被标红，机构一个都没抓到——原来的机构名正则只认
    「…有限公司／集团／研究院」这几个中文后缀。
    **编造的机构归属比编造的数字更能骗人**：数字还会被数字清单标红，而一句
    「IDC 预计」读上去就像已经有出处了。
    """

    def test_catches_research_houses_that_have_no_attached_excerpt(self) -> None:
        cases = [
            ("钉钉企业版报价高于同业，麦肯锡的调研显示该定价偏高。", "麦肯锡"),
            ("Cursor 单次请求折合 0.042 美元，Gartner 据此认为其成本更低。", "Gartner"),
            ("苹果本季服务收入创高，IDC 预计下季继续增长。", "IDC"),
            ("本市企业已达 1274 家，中信证券预计产值将破 640 亿元。", "中信证券"),
            ("零售规模持续扩张，欧睿国际预计 2028 年再上一个台阶。", "欧睿国际"),
        ]
        for text, expected in cases:
            with self.subTest(org=expected):
                names = [item["text"] for item in unsourced_orgs(text, [])]
                self.assertIn(expected, names)

    def test_name_is_trimmed_not_swallowed_by_the_sentence(self) -> None:
        # 「麦肯锡的调研显示」要标成「麦肯锡」，不是「麦肯锡的调研」
        names = [item["text"] for item in unsourced_orgs("麦肯锡的调研显示情况在变。", [])]
        self.assertIn("麦肯锡", names)
        self.assertNotIn("麦肯锡的调研", names)

    def test_attached_excerpt_suppresses_the_flag(self) -> None:
        # 挂上了原话就不再是借来的权威。
        text = "IDC 预计下季继续增长。"
        self.assertTrue(unsourced_orgs(text, []))
        self.assertEqual(unsourced_orgs(text, ["IDC 预计下季继续增长。"]), [])

    def test_ordinary_narration_is_not_flagged(self) -> None:
        """宁可漏报也不要吵：一个老误报的提示，人两天就学会无视它。"""
        clean = [
            "该数字的定语是「据专业机构测算」，不是统计部门的口径。",
            "同一篇对阶段的判断是产业正在向成长期迈进。",
            "两份文档均未给出单次请求的货币价格，因此无法比较单位成本。",
            "本节答到了两家计费口径的差异这一层。",
            "分品种看，宠物饲料产量增长，其他饲料产量也增长。",
        ]
        for text in clean:
            with self.subTest(text=text[:16]):
                self.assertEqual(unsourced_orgs(text, []), [], text)


if __name__ == "__main__":
    unittest.main()
