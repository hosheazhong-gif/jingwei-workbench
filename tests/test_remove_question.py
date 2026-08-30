from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.material_question import assign_material_question
from app.application.question_progress import (
    QuestionProgressError,
    remove_research_question,
)
from app.application.remove_source import remove_source
from app.projections.workbench import build_workbench_projection


class RemoveResearchQuestionTest(unittest.TestCase):
    """拆错的问题要能真去掉；归着材料的不许悄悄删。

    现场缺陷（docs/20 §6，2026-08-26）：产品所有者做「AIGC 有哪些新兴公司」时
    第一条问题拆歪了，后面搜回来的材料全跟着歪。当时问题只能「这轮先不用」，
    没有任何清理手段，错问题和冗余材料会一直堆着。
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(Path(self.temp_dir.name) / "j.sqlite3")
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="AIGC 新兴公司",
            original_context="看看目前 AIGC 有什么突出的新兴公司。",
            questions=["先按什么标准圈这个名单", "拆歪的那一条"],
        )
        self.project_id = created["project_id"]
        bench = build_workbench_projection(self.repository, self.project_id)
        self.keep = bench["questions"][0]["id"]
        self.wrong = bench["questions"][1]["id"]

    def _questions(self) -> list[str]:
        bench = build_workbench_projection(self.repository, self.project_id)
        return [item["id"] for item in bench["questions"] + bench["deferred_questions"]]

    def test_a_question_with_nothing_on_it_can_really_be_removed(self) -> None:
        self.assertIn(self.wrong, self._questions())
        result = remove_research_question(self.repository, self.wrong)
        self.assertNotIn(self.wrong, self._questions())
        self.assertIn(self.keep, self._questions())
        self.assertIn("已去掉", result["confirmation"]["message"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])

    def test_a_question_with_material_on_it_is_refused_with_the_count(self) -> None:
        capture_local_source(
            self.repository,
            self.project_id,
            title="搜歪了的材料",
            uploaded_name="a.txt",
            uploaded_bytes="随便一段公开材料原文。".encode("utf-8"),
            question_id=self.wrong,
        )
        with self.assertRaises(QuestionProgressError) as caught:
            remove_research_question(self.repository, self.wrong)
        message = str(caught.exception)
        self.assertIn("1 份材料", message)
        # 拒绝时要把两条出路都说出来，而不是只说不行
        self.assertIn("改归到别的问题", message)
        self.assertIn("这轮先不用", message)
        self.assertIn(self.wrong, self._questions())

    def test_moving_the_material_away_then_removing_works(self) -> None:
        """这就是拆错之后该走的路：材料先归到对的问题，再删掉错的那条。"""
        captured = capture_local_source(
            self.repository,
            self.project_id,
            title="搜歪了的材料",
            uploaded_name="a.txt",
            uploaded_bytes="随便一段公开材料原文。".encode("utf-8"),
            question_id=self.wrong,
        )
        assign_material_question(
            self.repository,
            source_id=captured["source"]["id"],
            question_id=self.keep,
        )
        remove_research_question(self.repository, self.wrong)
        self.assertNotIn(self.wrong, self._questions())
        # 材料还在，只是换了归属——删问题不许把材料一起带走
        bench = build_workbench_projection(self.repository, self.project_id)
        titles = [item["title"] for item in bench["materials"]["sources"]]
        self.assertIn("搜歪了的材料", titles)

    def test_removing_the_material_first_also_works(self) -> None:
        captured = capture_local_source(
            self.repository,
            self.project_id,
            title="没用的材料",
            uploaded_name="b.txt",
            uploaded_bytes="另一段公开材料原文。".encode("utf-8"),
            question_id=self.wrong,
        )
        remove_source(self.repository, captured["source"]["id"])
        remove_research_question(self.repository, self.wrong)
        self.assertNotIn(self.wrong, self._questions())

    def test_removing_a_question_does_not_touch_the_draft(self) -> None:
        before = [
            item["current_text"]
            for item in build_workbench_projection(self.repository, self.project_id)["blocks"]
        ]
        remove_research_question(self.repository, self.wrong)
        after = [
            item["current_text"]
            for item in build_workbench_projection(self.repository, self.project_id)["blocks"]
        ]
        self.assertEqual(before, after)

    def test_a_missing_question_says_so(self) -> None:
        with self.assertRaises(QuestionProgressError) as caught:
            remove_research_question(self.repository, "RQ-NOPE")
        self.assertIn("不存在", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
