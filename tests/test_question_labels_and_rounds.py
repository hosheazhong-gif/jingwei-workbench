from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.candidate_source import (
    CandidateSourceError,
    capture_web_candidate,
    discard_web_candidate,
    open_web_candidate,
    restore_web_candidate,
)
from app.application.attach_claim import ClaimAttachError, attach_claim_to_block
from app.application.capture_source import capture_manager_feedback
from app.application.create_project import create_project
from app.application.draft_suggestion import (
    MIN_PROMPT_EXCERPT_CHARS,
    PROMPT_EXCERPT_BUDGET_CHARS,
    _excerpt_char_limit,
    _label_excerpt,
)
from app.application.research_round import (
    ResearchRoundError,
    close_research_round,
    reopen_research_round,
)
from app.application.review_block import adopt_revision, propose_block_revision
from app.application.round_questions import rename_research_question
from app.application.source_snapshot import (
    read_source_snapshot,
    snapshot_plain_text,
)
from app.application.verify_claim import update_claim_verification
from app.domain import ProvenanceScope
from app.projections.workbench import build_workbench_projection


class QuestionLabelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(
            Path(self.temp_dir.name) / "jingwei.sqlite3",
            files_root=Path(self.temp_dir.name) / "files",
        )
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=[
                "机器人手臂产业链从上游核心零部件到中游本体制造、下游系统集成，"
                "各环节的国产化率、主要瓶颈和代表性企业分别是什么？"
            ],
        )
        self.project_id = created["project_id"]
        self.question_id = created["brief_projection"]["questions"][0]["id"]
        self.block_id = created["report"]["blocks"][0]["id"]

    def _question(self) -> dict:
        workbench = build_workbench_projection(self.repository, self.project_id)
        return workbench["questions"][0]

    def test_missing_label_falls_back_to_a_truncated_question(self) -> None:
        item = self._question()
        self.assertIsNone(item["label"])
        # 不替既有问题编短名，只截断显示
        self.assertTrue(item["short_label"].endswith("…"))
        self.assertLessEqual(len(item["short_label"]), 15)
        self.assertTrue(item["question"].startswith(item["short_label"][:-1]))

    def test_rename_sets_a_short_label_without_touching_the_question(self) -> None:
        rename_research_question(
            self.repository,
            self.question_id,
            self._question()["question"],
            label="零件国产化率",
        )
        item = self._question()
        self.assertEqual(item["label"], "零件国产化率")
        self.assertEqual(item["short_label"], "零件国产化率")
        self.assertIn("国产化率、主要瓶颈", item["question"])

    def test_long_label_is_cut_not_rejected(self) -> None:
        rename_research_question(
            self.repository,
            self.question_id,
            self._question()["question"],
            label="这是一个非常非常长的短名超过了十四个字的上限",
        )
        self.assertEqual(len(self._question()["label"]), 14)


class RevisionRoundTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(
            Path(self.temp_dir.name) / "jingwei.sqlite3",
            files_root=Path(self.temp_dir.name) / "files",
        )
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        self.project_id = created["project_id"]
        self.block_id = created["report"]["blocks"][0]["id"]

    def _rounds(self) -> list[tuple[int, int]]:
        with self.repository.connect() as connection:
            rows = connection.execute(
                """
                SELECT version, round_index FROM deliverable_block_revisions
                WHERE deliverable_block_id = ? ORDER BY version
                """,
                (self.block_id,),
            ).fetchall()
        return [(int(row["version"]), int(row["round_index"])) for row in rows]

    def test_revisions_carry_the_round_they_were_written_in(self) -> None:
        first = propose_block_revision(
            self.repository, self.block_id, body="第一轮给经理的稿。"
        )
        adopt_revision(
            self.repository, self.block_id, first["proposed_revision"]["version"]
        )
        self.assertEqual(self._rounds(), [(1, 1), (2, 1)])

        close_research_round(self.repository, self.project_id)
        second = propose_block_revision(
            self.repository, self.block_id, body="第二轮补料之后重写的一版。"
        )
        adopt_revision(
            self.repository, self.block_id, second["proposed_revision"]["version"]
        )
        # 段落对象仍只有一套，第一轮那一版留在库里，标着第 1 轮
        self.assertEqual(self._rounds(), [(1, 1), (2, 1), (3, 2)])
        with self.repository.connect() as connection:
            bodies = {
                int(row["round_index"]): row["body"]
                for row in connection.execute(
                    "SELECT round_index, body FROM deliverable_block_revisions"
                    " WHERE deliverable_block_id = ? AND version > 1",
                    (self.block_id,),
                )
            }
        self.assertEqual(bodies[1], "第一轮给经理的稿。")
        self.assertEqual(bodies[2], "第二轮补料之后重写的一版。")

    def test_only_one_set_of_blocks_after_closing_a_round(self) -> None:
        before = build_workbench_projection(self.repository, self.project_id)["blocks"]
        close_research_round(self.repository, self.project_id)
        after = build_workbench_projection(self.repository, self.project_id)["blocks"]
        self.assertEqual(
            [item["id"] for item in before], [item["id"] for item in after]
        )


class SetAsideDrawerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(
            Path(self.temp_dir.name) / "jingwei.sqlite3",
            files_root=Path(self.temp_dir.name) / "files",
        )
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        self.project_id = created["project_id"]
        self.candidate_id = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/one",
            title="先收着",
        )["candidate_id"]

    def _materials(self) -> dict:
        return build_workbench_projection(self.repository, self.project_id)["materials"]

    def test_set_aside_candidate_moves_to_the_drawer_and_comes_back(self) -> None:
        discard_web_candidate(self.repository, self.candidate_id)
        materials = self._materials()
        self.assertNotIn(
            self.candidate_id, [item["id"] for item in materials["candidates"]]
        )
        self.assertEqual(
            [item["id"] for item in materials["set_aside"]], [self.candidate_id]
        )

        restored = restore_web_candidate(self.repository, self.candidate_id)
        self.assertTrue(restored["confirmation"]["current_text_unchanged"])
        self.assertTrue(restored["confirmation"]["verification_status_unchanged"])
        materials = self._materials()
        self.assertIn(
            self.candidate_id, [item["id"] for item in materials["candidates"]]
        )
        self.assertEqual(materials["set_aside"], [])

    def test_restoring_keeps_the_fact_that_it_was_opened(self) -> None:
        open_web_candidate(self.repository, self.candidate_id)
        discard_web_candidate(self.repository, self.candidate_id)
        restore_web_candidate(self.repository, self.candidate_id)
        item = next(
            row
            for row in self._materials()["candidates"]
            if row["id"] == self.candidate_id
        )
        self.assertEqual(item["status"], "opened")
        self.assertTrue(item["can_promote"])

    def test_restoring_something_already_in_the_box_is_refused(self) -> None:
        with self.assertRaises(CandidateSourceError):
            restore_web_candidate(self.repository, self.candidate_id)


class LongExcerptPromptTest(unittest.TestCase):
    """人粘进来的长原话：库里不动，发给模型时按总预算截，并说明截了。"""

    def test_one_long_excerpt_still_goes_in_whole(self) -> None:
        # 一节只挂一条 5636 字的政策长文时不该被砍——稿要写细正靠它
        body = "政" * 5636
        self.assertIsNone(_excerpt_char_limit([body]))
        self.assertNotIn("只给出前", _label_excerpt(body, "公开网页", None))

    def test_many_long_excerpts_share_the_budget(self) -> None:
        items = ["政" * 5000 for _ in range(6)]
        limit = _excerpt_char_limit(items)
        self.assertEqual(limit, PROMPT_EXCERPT_BUDGET_CHARS // 6)
        line = _label_excerpt(items[0], "公开网页", limit)
        self.assertIn("这条原话共 5000 字", line)
        self.assertIn("后面的内容不得当作已知", line)
        # 截断只发生在发给模型的那一份上
        self.assertEqual(len(items[0]), 5000)

    def test_every_excerpt_keeps_a_floor(self) -> None:
        items = ["政" * 4000 for _ in range(200)]
        self.assertEqual(_excerpt_char_limit(items), MIN_PROMPT_EXCERPT_CHARS)


class ManagerFeedbackTest(unittest.TestCase):
    """经理反馈：一份核心材料，归属自成一档，不是客户口径也不是外部证据。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(
            Path(self.temp_dir.name) / "jingwei.sqlite3",
            files_root=Path(self.temp_dir.name) / "files",
        )
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        self.project_id = created["project_id"]
        self.block_id = created["report"]["blocks"][0]["id"]

    def test_feedback_becomes_a_readable_source_in_the_box(self) -> None:
        saved = capture_manager_feedback(
            self.repository,
            self.project_id,
            text="第一轮太泛了，只要长三角，别写全国。",
        )
        source_id = saved["source"]["id"]
        body, content_type = read_source_snapshot(self.repository, source_id)
        self.assertIn("只要长三角", snapshot_plain_text(body, content_type))
        row = self.repository.get_source(source_id)
        self.assertEqual(row["kind"], "manager_feedback")
        self.assertIn("经理反馈", row["title"])

    def test_feedback_claim_carries_its_own_provenance(self) -> None:
        source_id = capture_manager_feedback(
            self.repository, self.project_id, text="只要长三角，别写全国。"
        )["source"]["id"]
        attached = attach_claim_to_block(
            self.repository,
            self.block_id,
            source_id=source_id,
            excerpt="只要长三角，别写全国。",
            text="只要长三角，别写全国。",
            provenance_scope=ProvenanceScope.MANAGER_FEEDBACK.value,
        )
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT provenance_scope, delivery_rule, verification_status"
                " FROM claims WHERE id = ?",
                (attached["claim_id"],),
            ).fetchone()
        self.assertEqual(row["provenance_scope"], "manager_feedback")
        self.assertIn("经理反馈", row["delivery_rule"])
        self.assertNotIn("客户提供", row["delivery_rule"])
        self.assertEqual(row["verification_status"], "captured")

    def test_unknown_provenance_is_refused(self) -> None:
        source_id = capture_manager_feedback(
            self.repository, self.project_id, text="只要长三角。"
        )["source"]["id"]
        with self.assertRaises(ClaimAttachError):
            attach_claim_to_block(
                self.repository,
                self.block_id,
                source_id=source_id,
                excerpt="只要长三角。",
                provenance_scope="hearsay",
            )

    def test_check_flags_feedback_pushed_up_to_evidence(self) -> None:
        source_id = capture_manager_feedback(
            self.repository, self.project_id, text="只要长三角，别写全国。"
        )["source"]["id"]
        attached = attach_claim_to_block(
            self.repository,
            self.block_id,
            source_id=source_id,
            excerpt="只要长三角，别写全国。",
            provenance_scope=ProvenanceScope.MANAGER_FEEDBACK.value,
        )
        clean = self._checks()
        self.assertEqual(clean["feedback_as_evidence"], [])
        update_claim_verification(
            self.repository,
            self.block_id,
            attached["claim_id"],
            verification_status="corroborated",
        )
        flagged = self._checks()
        self.assertEqual(len(flagged["feedback_as_evidence"]), 1)
        self.assertEqual(
            flagged["feedback_as_evidence"][0]["claim_id"], attached["claim_id"]
        )

    def _checks(self) -> dict:
        workbench = build_workbench_projection(self.repository, self.project_id)
        block = next(
            item for item in workbench["blocks"] if item["id"] == self.block_id
        )
        return block["checks"]


class ReopenRoundTest(unittest.TestCase):
    """收口不是单向门：这一轮还没开始就能退回上一轮。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(
            Path(self.temp_dir.name) / "jingwei.sqlite3",
            files_root=Path(self.temp_dir.name) / "files",
        )
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        self.project_id = created["project_id"]
        self.block_id = created["report"]["blocks"][0]["id"]

    def _round(self) -> int:
        return build_workbench_projection(self.repository, self.project_id)[
            "current_round"
        ]

    def test_empty_new_round_can_go_back(self) -> None:
        close_research_round(self.repository, self.project_id)
        self.assertEqual(self._round(), 2)
        result = reopen_research_round(self.repository, self.project_id)
        self.assertEqual(result["current_round"], 1)
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        workbench = build_workbench_projection(self.repository, self.project_id)
        # 上一轮的问题原样回到台面，没有被删过也没有改状态
        self.assertEqual(len(workbench["questions"]), 1)
        self.assertEqual(workbench["archived_rounds"], [])

    def test_first_round_has_nothing_to_go_back_to(self) -> None:
        with self.assertRaises(ResearchRoundError):
            reopen_research_round(self.repository, self.project_id)

    def test_started_round_refuses_to_go_back(self) -> None:
        close_research_round(self.repository, self.project_id)
        propose_block_revision(
            self.repository, self.block_id, body="第二轮已经写过的一版。"
        )
        with self.assertRaises(ResearchRoundError) as caught:
            reopen_research_round(self.repository, self.project_id)
        self.assertIn("已经开始", str(caught.exception))
        self.assertEqual(self._round(), 2)


if __name__ == "__main__":
    unittest.main()
