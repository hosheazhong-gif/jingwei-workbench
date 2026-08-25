from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.http_draft import parse_round_questions, _ROUND_QUESTION_PROMPT
from app.adapters.sqlite_repository import SqliteRepository
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.application.question_progress import (
    QuestionProgressError,
    set_question_target_block,
)
from app.application.round_questions import adopt_round_questions
from app.projections.workbench import build_workbench_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class QuestionTargetBlockTest(unittest.TestCase):
    """拆问题时先定「这条落在稿的哪一节」；定不了就留空，绝不猜。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(Path(self.temp_dir.name) / "jingwei.sqlite3")
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)
        self.bench = build_workbench_projection(self.repository, self.project_id)
        self.blocks = {item["title"]: item["id"] for item in self.bench["blocks"]}

    def test_prompt_asks_for_section_and_allows_blank(self) -> None:
        self.assertIn("section", _ROUND_QUESTION_PROMPT)
        self.assertIn("看不出该落哪一节就留空", _ROUND_QUESTION_PROMPT)

    def test_parse_carries_section(self) -> None:
        parsed = parse_round_questions(
            '{"questions":[{"question":"零件清不清","enough_for_now":"缺上游",'
            '"section":"项目问题"}]}'
        )
        self.assertEqual(parsed[0]["section"], "项目问题")

    def test_adopt_maps_matching_section_to_block(self) -> None:
        result = adopt_round_questions(
            self.repository,
            self.project_id,
            [
                {
                    "question": "零件和上游清不清楚",
                    "enough_for_now": "能回到具体环节",
                    "section": "项目问题",
                }
            ],
        )
        question = next(
            item
            for item in result["workbench"]["questions"]
            if item["id"] in result["question_ids"]
        )
        self.assertEqual(question["target_block_id"], self.blocks["项目问题"])
        self.assertEqual(question["target_section"], "项目问题")

    def test_adopt_ignores_spacing_and_punctuation_in_section_name(self) -> None:
        result = adopt_round_questions(
            self.repository,
            self.project_id,
            [{"question": "案例够不够近", "section": " 案例、启示 "}],
        )
        question = next(
            item
            for item in result["workbench"]["questions"]
            if item["id"] in result["question_ids"]
        )
        self.assertEqual(question["target_block_id"], self.blocks["案例启示"])

    def test_unmatched_section_is_left_empty_never_guessed(self) -> None:
        result = adopt_round_questions(
            self.repository,
            self.project_id,
            [
                {"question": "本地配套跟不跟得上", "section": "区位与配套"},
                {"question": "政策口径是什么"},
            ],
        )
        added = [
            item
            for item in result["workbench"]["questions"]
            if item["id"] in result["question_ids"]
        ]
        self.assertEqual(len(added), 2)
        for question in added:
            self.assertIsNone(question["target_block_id"])
            self.assertIsNone(question["target_section"])
            self.assertEqual(question["target_section_label"], "还没定落在哪一节")

    def test_person_can_set_and_clear_the_section(self) -> None:
        question_id = self.bench["questions"][0]["id"]
        target = self.blocks["候选方向"]
        result = set_question_target_block(self.repository, question_id, target)
        self.assertIn("候选方向", result["confirmation"]["message"])
        question = next(
            item for item in result["workbench"]["questions"] if item["id"] == question_id
        )
        self.assertEqual(question["target_block_id"], target)
        cleared = set_question_target_block(self.repository, question_id, "")
        question = next(
            item
            for item in cleared["workbench"]["questions"]
            if item["id"] == question_id
        )
        self.assertIsNone(question["target_block_id"])
        self.assertEqual(question["target_section_label"], "还没定落在哪一节")

    def test_section_from_another_project_is_refused(self) -> None:
        other = create_project(
            self.repository,
            name="别的题目",
            original_context="看看别的产业链条。",
        )
        other_block = other["report"]["blocks"][0]["id"]
        question_id = self.bench["questions"][0]["id"]
        with self.assertRaises(QuestionProgressError) as caught:
            set_question_target_block(self.repository, question_id, other_block)
        self.assertIn("不在这个题目里", str(caught.exception))

    def test_setting_the_section_does_not_touch_the_draft_or_checks(self) -> None:
        before = build_workbench_projection(self.repository, self.project_id)["blocks"]
        set_question_target_block(
            self.repository, self.bench["questions"][0]["id"], self.blocks["项目问题"]
        )
        after = build_workbench_projection(self.repository, self.project_id)["blocks"]
        self.assertEqual(
            [item["current_text"] for item in before],
            [item["current_text"] for item in after],
        )


if __name__ == "__main__":
    unittest.main()
