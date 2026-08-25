from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.http_draft import parse_round_questions, _ROUND_QUESTION_PROMPT
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.application.round_questions import (
    RoundQuestionError,
    adopt_round_questions,
    draft_round_questions,
    rename_research_question,
)
from app.projections.brief import build_brief_projection
from app.projections.report import build_review_context
from app.projections.workbench import DEFERRED_STATUS, build_workbench_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ScriptedQuestionAdapter:
    key = "scripted-questions"

    def propose(self, context):
        self.context = context
        return [
            {
                "question": "零件和上游清不清楚",
                "enough_for_now": "能回到具体环节，而不是产业口号",
            },
            {
                "question": "政策材料能不能落到这条链",
                "enough_for_now": "原话没点到落地口径",
            },
        ]


class RoundQuestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.synthetic_id = import_sample(self.repository, SAMPLE_PATH)

    def test_parse_keeps_gap_note_and_prompt_forbids_template_mismatch(self) -> None:
        parsed = parse_round_questions(
            '{"questions":[{"question":"零件清不清","enough_for_now":"缺上游环节"}]}'
        )
        self.assertEqual(parsed[0]["question"], "零件清不清")
        self.assertEqual(parsed[0]["enough_for_now"], "缺上游环节")
        self.assertIn("禁止把无关模板问题套进来", _ROUND_QUESTION_PROMPT)
        self.assertIn("上一轮已经问过", _ROUND_QUESTION_PROMPT)
        self.assertIn("enough_for_now", _ROUND_QUESTION_PROMPT)

    def test_draft_does_not_write_questions_or_synthetic(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条，了解政策和前景。",
        )
        before = build_review_context(
            self.repository, created["report"]["blocks"][0]["id"]
        )
        adapter = ScriptedQuestionAdapter()
        result = draft_round_questions(
            self.repository, created["project_id"], adapter=adapter
        )
        after = build_review_context(
            self.repository, created["report"]["blocks"][0]["id"]
        )
        self.assertEqual(adapter.context["task"], "round_questions")
        self.assertIn("打通机器人手臂", adapter.context["original_context"])
        self.assertTrue(adapter.context["template_hints"])
        self.assertEqual(result["questions"][0]["question"], "零件和上游清不清楚")
        self.assertEqual(build_brief_projection(self.repository, created["project_id"])["questions"], [])
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        synthetic = build_brief_projection(self.repository, self.synthetic_id)
        self.assertTrue(synthetic["questions"])

    def test_adopt_replaces_template_questions_without_delete(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["园区现在的租户、空间和设施结构清不清楚"],
        )
        project_id = created["project_id"]
        old_id = created["brief_projection"]["questions"][0]["id"]
        drafted = draft_round_questions(
            self.repository, project_id, adapter=ScriptedQuestionAdapter()
        )
        result = adopt_round_questions(
            self.repository, project_id, drafted["questions"]
        )
        brief = build_brief_projection(self.repository, project_id)
        workbench = build_workbench_projection(self.repository, project_id)
        active = [item for item in brief["questions"] if item["status"] != DEFERRED_STATUS]
        deferred = [item for item in brief["questions"] if item["status"] == DEFERRED_STATUS]
        self.assertEqual(
            [item["question"] for item in active],
            ["零件和上游清不清楚", "政策材料能不能落到这条链"],
        )
        self.assertEqual(deferred[0]["id"], old_id)
        self.assertEqual(len(workbench["deferred_questions"]), 1)
        self.assertIn("零件和上游清不清楚", [item["question"] for item in workbench["questions"]])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])

    def test_rename_does_not_delete_or_rewrite_draft(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["先看零件"],
        )
        question_id = created["brief_projection"]["questions"][0]["id"]
        block_id = created["report"]["blocks"][0]["id"]
        before = build_review_context(self.repository, block_id)
        result = rename_research_question(self.repository, question_id, "零件和上游清不清楚")
        after = build_review_context(self.repository, block_id)
        self.assertEqual(
            build_brief_projection(self.repository, created["project_id"])["questions"][0]["question"],
            "零件和上游清不清楚",
        )
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(result["confirmation"]["record_kind"], "rename_question")

    def test_unconfigured_refuses_to_invent(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
        )
        with patch.dict(os.environ, {"JINGWEI_DRAFT_API_KEY": ""}, clear=False):
            with self.assertRaises(RoundQuestionError) as raised:
                draft_round_questions(self.repository, created["project_id"])
        self.assertIn("还没接模型", str(raised.exception))
        self.assertEqual(
            build_brief_projection(self.repository, created["project_id"])["questions"],
            [],
        )


class RoundQuestionHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["园区租户清不清楚"],
        )
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_unconfigured_draft_does_not_write(self) -> None:
        with patch.dict(os.environ, {"JINGWEI_DRAFT_API_KEY": ""}, clear=False):
            status, payload = self._post(
                f"/projects/{self.created['project_id']}/round-questions/draft",
                {},
            )
        self.assertEqual(status, 400)
        self.assertIn("还没接模型", payload["error"])
        self.assertEqual(
            [item["question"] for item in build_brief_projection(self.repository, self.created["project_id"])["questions"]],
            ["园区租户清不清楚"],
        )

    def test_http_adopt_and_rename(self) -> None:
        status, payload = self._post(
            f"/projects/{self.created['project_id']}/round-questions/adopt",
            {
                "questions": [
                    {"question": "零件和上游清不清楚", "enough_for_now": "能回到环节"}
                ]
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["question_ids"])
        workbench = payload["workbench"]
        self.assertEqual(workbench["questions"][0]["question"], "零件和上游清不清楚")
        self.assertEqual(workbench["deferred_questions"][0]["question"], "园区租户清不清楚")
        question_id = payload["question_ids"][0]
        renamed_status, renamed = self._post(
            f"/research-questions/{question_id}/rename",
            {"question": "上游零件和模组清不清楚"},
        )
        self.assertEqual(renamed_status, 200)
        self.assertEqual(
            renamed["workbench"]["questions"][0]["question"],
            "上游零件和模组清不清楚",
        )

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = Request(
            self.server.origin + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = json.loads(error.read().decode("utf-8"))
            error.close()
            return error.code, body


class QuestionTrimTest(unittest.TestCase):
    """问题不许从中间切断：账本里留一句半截话，经理看到的也是半截话。"""

    def test_long_question_is_cut_at_a_sentence_end(self) -> None:
        from app.application.round_questions import _trim_to_sentence

        long = (
            "上一轮稿中，政策部分仅列出政策方向，未提供具体落地条件和时效性。"
            "这一轮能否获取至少一项具体政策的官方文件或公告，明确其申报条件、执行年限和适用范围？"
        )
        trimmed = _trim_to_sentence(long, 40)
        self.assertTrue(trimmed.endswith("。"))
        self.assertIn("未提供具体落地条件和时效性", trimmed)

    def test_no_sentence_end_means_no_cut(self) -> None:
        from app.application.round_questions import _trim_to_sentence

        run_on = "国产化率" * 40
        self.assertEqual(_trim_to_sentence(run_on, 30), run_on)

    def test_short_question_is_left_alone(self) -> None:
        from app.application.round_questions import (
            MAX_QUESTION_CHARS,
            _trim_to_sentence,
        )

        short = "上游核心零部件的国产化率是多少？"
        self.assertEqual(_trim_to_sentence(short, MAX_QUESTION_CHARS), short)
