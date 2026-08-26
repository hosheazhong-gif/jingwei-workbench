from __future__ import annotations

import re
import unittest
from pathlib import Path

from app.projections.templates import build_template_list_projection
from app.templates.registry import VERIFICATION_LEVELS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
README = PROJECT_ROOT / "README.md"


class ReadmeMatchesTheProductTest(unittest.TestCase):
    """README 是给外面人看的第一眼，不许跟代码对不上。

    上线第一版时踩过：README 把模板叫成「品牌定位对标」「产品功能对标」，
    而实际模板名是「竞品分析（定位与主张对标）」「竞品分析（产品与功能对标）」。
    用户照 README 找不到那个模板，只会以为是自己看错了。
    """

    def setUp(self) -> None:
        self.text = README.read_text(encoding="utf-8")
        self.listing = build_template_list_projection()

    def test_every_template_name_appears_verbatim(self) -> None:
        for item in self.listing["templates"]:
            name = item["name"]
            stem, _, rest = name.partition("（")
            sub = rest.rstrip("）")
            self.assertIn(stem, self.text, f"README 里没有模板「{name}」")
            self.assertIn(sub, self.text, f"README 里模板「{name}」的括号部分对不上")

    def test_template_count_is_stated_correctly(self) -> None:
        count = len(self.listing["templates"])
        stated = re.findall(r"([一二三四五六七八九十\d]+)\s*个模板", self.text)
        stated += re.findall(r"([一二三四五六七八九十\d]+)个模板", self.text)
        digits = {"七": 7, "六": 6, "五": 5, "八": 8}
        for raw in stated:
            value = digits.get(raw, None)
            if value is None and raw.isdigit():
                value = int(raw)
            if value is not None:
                self.assertEqual(value, count, f"README 说有 {raw} 个模板，实际 {count} 个")

    def test_verification_labels_match_the_code(self) -> None:
        # 三档的说法必须跟 registry 里的一致，不许 README 自己另编一套。
        for key in ("loop_walked", "walked_by_hand"):
            label = VERIFICATION_LEVELS[key]
            head = label.split("，")[0]
            self.assertIn(head, self.text, f"README 里没有「{head}」这一档的说法")

    def test_readme_says_what_the_tool_will_not_do(self) -> None:
        # 边界写得出来比功能多更有说服力，而且能挡住用错的人。
        for word in ("不做计算", "不做表", "127.0.0.1:8765"):
            self.assertIn(word, self.text, f"README 少了「{word}」")

    def test_readme_does_not_leak_internal_documents(self) -> None:
        # docs/ 不进版本库；README 是给外面人看的，不该往里指。
        for word in ("PRD", "docs/14", "docs/20", "docs/24", "流水账"):
            self.assertNotIn(word, self.text, f"README 里出现了内部文档字样「{word}」")


if __name__ == "__main__":
    unittest.main()
