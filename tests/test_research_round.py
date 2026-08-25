from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.attach_claim import attach_claim_to_block
from app.application.candidate_source import (
    capture_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.create_project import create_project
from app.application.draft_suggestion import DraftSuggestionError, draft_block_revision
from app.application.import_sample import import_sample
from app.application.question_progress import add_research_question
from app.application.research_round import ResearchRoundError, close_research_round
from app.application.round_questions import adopt_round_questions, draft_round_questions
from app.application.search_materials import search_project_materials
from app.application.source_snapshot import read_source_snapshot
from app.projections.workbench import build_workbench_projection
from tests.test_draft_suggestion import MemoryWebAdapter, ScriptedRevisionAdapter
from tests.test_search_materials import FakeSearchAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ResearchRoundTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(
            self.database_path, files_root=Path(self.temp_dir.name) / "files"
        )
        self.repository.migrate()
        self.synthetic_id = import_sample(self.repository, SAMPLE_PATH)

    def test_migration_keeps_old_text_hash_and_sets_round_one(self) -> None:
        with self.repository.connect() as connection:
            project = connection.execute(
                "SELECT current_round, schema_version FROM projects WHERE id = ?",
                (self.synthetic_id,),
            ).fetchone()
            claim = connection.execute(
                "SELECT text, verification_status FROM claims WHERE id = 'C-002'"
            ).fetchone()
            source = connection.execute(
                "SELECT content_hash, research_question_id FROM sources WHERE id = 'S-002'"
            ).fetchone()
            block = connection.execute(
                "SELECT current_text FROM deliverable_blocks WHERE id = 'DB-001'"
            ).fetchone()
            versions = {
                row["schema_version"]
                for row in connection.execute("SELECT schema_version FROM schema_migrations")
            }
        self.assertEqual(project["current_round"], 1)
        self.assertEqual(project["schema_version"], "0.8")
        self.assertIn("0.6", versions)
        self.assertEqual(claim["verification_status"], "captured")
        self.assertTrue(claim["text"])
        self.assertTrue(source["content_hash"])
        self.assertIsNone(source["research_question_id"])
        self.assertIn("生产型租户", block["current_text"])

    def test_close_round_archives_questions_without_rewriting_draft(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        project_id = created["project_id"]
        before = created["report"]["blocks"][0]["current_text"]
        old_id = created["brief_projection"]["questions"][0]["id"]
        result = close_research_round(self.repository, project_id)
        workbench = result["workbench"]
        self.assertEqual(result["closed_round"], 1)
        self.assertEqual(result["current_round"], 2)
        self.assertEqual(workbench["current_round"], 2)
        self.assertEqual(workbench["questions"], [])
        self.assertEqual(workbench["archived_rounds"][0]["questions"][0]["id"], old_id)
        self.assertFalse(workbench["can_close_round"])
        self.assertEqual(
            workbench["blocks"][0]["current_text"],
            before,
        )
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        with self.assertRaises(ResearchRoundError):
            close_research_round(self.repository, project_id)

    def test_next_round_questions_do_not_repeat_archived(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        project_id = created["project_id"]
        close_research_round(self.repository, project_id)

        class NextRoundAdapter:
            key = "next-round"

            def propose(self, context):
                self.context = context
                return [
                    {
                        "question": "谁在做关节模组",
                        "enough_for_now": "能落到厂家而不是口号",
                    }
                ]

        adapter = NextRoundAdapter()
        drafted = draft_round_questions(self.repository, project_id, adapter=adapter)
        self.assertEqual(adapter.context["archived_questions"], ["零件和上游清不清楚"])
        self.assertEqual(adapter.context["questions"], [])
        adopted = adopt_round_questions(
            self.repository, project_id, drafted["questions"]
        )
        workbench = adopted["workbench"]
        self.assertEqual(workbench["current_round"], 2)
        self.assertEqual(
            [item["question"] for item in workbench["questions"]],
            ["谁在做关节模组"],
        )
        self.assertEqual(len(workbench["archived_rounds"][0]["questions"]), 1)
        self.assertEqual(workbench["deferred_questions"], [])

    def test_search_and_promote_keep_question_and_snapshot_is_readable(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        project_id = created["project_id"]
        question_id = created["brief_projection"]["questions"][0]["id"]
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/arm", "title": "Arm page", "snippet": "joint"}]
        )
        searched = search_project_materials(
            self.repository,
            project_id,
            question_id=question_id,
            search_adapter=searcher,
        )
        candidate_id = searched["added"][0]["id"]
        self.assertEqual(searched["added"][0]["research_question_id"], question_id)
        open_web_candidate(self.repository, candidate_id)
        promoted = promote_web_candidate(
            self.repository, candidate_id, adapter=MemoryWebAdapter()
        )
        source_id = promoted["source_id"]
        workbench = build_workbench_projection(self.repository, project_id)
        source = next(
            item for item in workbench["materials"]["sources"] if item["id"] == source_id
        )
        self.assertEqual(source["research_question_id"], question_id)
        self.assertEqual(source["question_label"], "零件和上游清不清楚")
        self.assertEqual(source["original_url"], "https://example.com/arm")
        self.assertTrue(source["can_view_snapshot"])
        body, content_type = read_source_snapshot(self.repository, source_id)
        self.assertIn("opened".encode("utf-8"), body)
        self.assertIn("html", content_type)

    def test_write_section_needs_hung_excerpt_for_selected_question(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        project_id = created["project_id"]
        question_id = created["brief_projection"]["questions"][0]["id"]
        block_id = created["report"]["blocks"][0]["id"]
        captured = capture_web_candidate(
            self.repository,
            project_id,
            url="https://example.com/arm",
            title="Arm page",
            question_id=question_id,
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=MemoryWebAdapter(),
        )
        with self.assertRaises(DraftSuggestionError) as raised:
            draft_block_revision(
                self.repository,
                block_id,
                adapter=ScriptedRevisionAdapter(),
                question_id=question_id,
            )
        self.assertIn("挂到这一节", str(raised.exception))
        attach_claim_to_block(
            self.repository,
            block_id,
            source_id=promoted["source_id"],
            excerpt="减速器占成本两成以上",
            text="减速器占成本两成以上",
        )
        adapter = ScriptedRevisionAdapter()
        result = draft_block_revision(
            self.repository,
            block_id,
            adapter=adapter,
            question_id=question_id,
        )
        self.assertTrue(result["confirmation"]["model_drafted"])
        self.assertEqual(adapter.context["focus_question"], "零件和上游清不清楚")
        self.assertIn("减速器占成本两成以上", adapter.context["excerpts"])

    def test_http_close_round_and_snapshot(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        project_id = created["project_id"]
        question_id = created["brief_projection"]["questions"][0]["id"]
        captured = capture_web_candidate(
            self.repository,
            project_id,
            url="https://example.com/arm",
            title="Arm page",
            question_id=question_id,
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=MemoryWebAdapter(),
        )
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        close_status, close_payload = self._post(
            server, f"/projects/{project_id}/rounds/close", {}
        )
        self.assertEqual(close_status, 200)
        self.assertEqual(close_payload["current_round"], 2)
        snap_status, content_type, body = self._get_bytes(
            server, f"/sources/{promoted['source_id']}/snapshot"
        )
        self.assertEqual(snap_status, 200)
        self.assertIn("html", content_type)
        self.assertIn(b"opened", body)
        self.assertIn("保存的网页快照".encode("utf-8"), body)

    def test_add_question_after_close_goes_to_new_round(self) -> None:
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        project_id = created["project_id"]
        close_research_round(self.repository, project_id)
        added = add_research_question(
            self.repository, project_id, question="谁在做关节模组"
        )
        workbench = added["workbench"]
        self.assertEqual(workbench["current_round"], 2)
        self.assertEqual(workbench["questions"][0]["question"], "谁在做关节模组")
        self.assertEqual(workbench["questions"][0]["round_index"], 2)

    def _post(self, server, path: str, payload: dict) -> tuple[int, dict]:
        request = Request(
            server.origin + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _get_bytes(self, server, path: str) -> tuple[int, str, bytes]:
        with urlopen(server.origin + path) as response:
            return (
                response.status,
                response.headers.get("Content-Type") or "",
                response.read(),
            )
