from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.candidate_source import (
    capture_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.attach_claim import attach_claim_to_block
from app.application.create_project import create_project
from app.application.excerpt_from_snapshot import (
    ExcerptFromSnapshotError,
    draft_snapshot_excerpts,
)
from app.application.material_question import (
    MaterialQuestionError,
    assign_materials_question,
)
from app.application.question_progress import defer_research_question
from app.application.review_block import adopt_revision, propose_block_revision
from app.projections.workbench import build_workbench_projection
from tests.test_candidate_source import MemoryWebAdapter


class EmptyBodyWebAdapter:
    """存下了受控副本，但页面是空壳（重 JS 的站常常这样）。"""

    key = "web_page"

    def snapshot(self, url: str, project_files: Path) -> dict:
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / "snapshot.bin"
        destination.write_bytes(
            b"<html><head><title>x</title></head><body>"
            b"<script>window.__DATA__={};</script></body></html>"
        )
        return {
            "file_name": "snapshot.bin",
            "original_url": url,
            "snapshot_path": destination,
            "content_hash": "0" * 64,
            "availability": "available",
        }


class MaterialQuestionTest(unittest.TestCase):
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
            questions=["零件和上游清不清楚", "政策给到哪一步"],
        )
        self.project_id = created["project_id"]
        questions = created["brief_projection"]["questions"]
        self.question_id = questions[0]["id"]
        self.other_question_id = questions[1]["id"]
        self.block_id = created["report"]["blocks"][0]["id"]

    def _candidate(self, url: str, title: str) -> str:
        captured = capture_web_candidate(
            self.repository, self.project_id, url=url, title=title
        )
        return captured["candidate_id"]

    def _promote(self, candidate_id: str, adapter) -> str:
        open_web_candidate(self.repository, candidate_id)
        return promote_web_candidate(
            self.repository, candidate_id, adapter=adapter
        )["source_id"]

    def _source(self, source_id: str) -> dict:
        workbench = build_workbench_projection(self.repository, self.project_id)
        return next(
            item
            for item in workbench["materials"]["sources"]
            if item["id"] == source_id
        )

    def test_snapshot_without_body_offers_neither_view_nor_scrape(self) -> None:
        candidate_id = self._candidate("https://example.com/spa", "空壳页")
        source_id = self._promote(candidate_id, EmptyBodyWebAdapter())
        source = self._source(source_id)
        self.assertFalse(source["can_view_snapshot"])
        self.assertFalse(source["can_scrape_snapshot"])
        self.assertEqual(
            source["snapshot_note"], "这份没能存下可读正文，只能打开链接。"
        )
        self.assertEqual(source["original_url"], "https://example.com/spa")
        with self.assertRaises(ExcerptFromSnapshotError):
            draft_snapshot_excerpts(
                self.repository,
                source_id,
                question_id=self.question_id,
                adapter=None,
            )

    def test_snapshot_with_body_still_offers_both(self) -> None:
        candidate_id = self._candidate("https://example.com/arm", "有正文的页")
        source_id = self._promote(candidate_id, MemoryWebAdapter())
        source = self._source(source_id)
        self.assertTrue(source["can_view_snapshot"])
        self.assertTrue(source["can_scrape_snapshot"])
        self.assertIsNone(source["snapshot_note"])

    def test_promoted_candidate_is_not_repeated_in_the_material_box(self) -> None:
        promoted_candidate = self._candidate("https://example.com/arm", "会升为来源")
        source_id = self._promote(promoted_candidate, MemoryWebAdapter())
        waiting_candidate = self._candidate("https://example.com/wait", "还没打开")
        workbench = build_workbench_projection(self.repository, self.project_id)
        candidate_ids = [
            item["id"] for item in workbench["materials"]["candidates"]
        ]
        source_ids = [item["id"] for item in workbench["materials"]["sources"]]
        self.assertIn(source_id, source_ids)
        self.assertNotIn(promoted_candidate, candidate_ids)
        self.assertIn(waiting_candidate, candidate_ids)

    def test_bulk_assign_only_touches_the_named_materials(self) -> None:
        source_id = self._promote(
            self._candidate("https://example.com/arm", "有正文的页"),
            MemoryWebAdapter(),
        )
        picked = self._candidate("https://example.com/one", "勾上的")
        left_alone = self._candidate("https://example.com/two", "没勾的")
        result = assign_materials_question(
            self.repository,
            self.project_id,
            source_ids=[source_id],
            candidate_ids=[picked],
            question_id=self.question_id,
        )
        self.assertTrue(result["confirmation"]["recorded"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertIn("2", result["confirmation"]["message"])
        workbench = result["workbench"]
        tagged = {
            item["id"]: item["research_question_id"]
            for item in workbench["materials"]["sources"]
            + workbench["materials"]["candidates"]
        }
        self.assertEqual(tagged[source_id], self.question_id)
        self.assertEqual(tagged[picked], self.question_id)
        self.assertIsNone(tagged[left_alone])

    def test_bulk_assign_refuses_empty_selection_and_foreign_material(self) -> None:
        with self.assertRaises(MaterialQuestionError):
            assign_materials_question(
                self.repository,
                self.project_id,
                source_ids=[],
                candidate_ids=[],
                question_id=self.question_id,
            )
        other = create_project(
            self.repository,
            name="另一道题",
            original_context="别的委托。",
            questions=["别的问题"],
        )
        foreign = capture_web_candidate(
            self.repository,
            other["project_id"],
            url="https://example.com/other",
            title="别题的材料",
        )["candidate_id"]
        with self.assertRaises(MaterialQuestionError):
            assign_materials_question(
                self.repository,
                self.project_id,
                candidate_ids=[foreign],
                question_id=self.question_id,
            )
        workbench = build_workbench_projection(self.repository, other["project_id"])
        still_untagged = next(
            item
            for item in workbench["materials"]["candidates"]
            if item["id"] == foreign
        )
        self.assertIsNone(still_untagged["research_question_id"])

    def test_bulk_assign_refuses_a_question_put_aside_this_round(self) -> None:
        picked = self._candidate("https://example.com/one", "勾上的")
        defer_research_question(self.repository, self.other_question_id)
        with self.assertRaises(MaterialQuestionError):
            assign_materials_question(
                self.repository,
                self.project_id,
                candidate_ids=[picked],
                question_id=self.other_question_id,
            )


    def test_section_counts_material_hung_after_it_was_last_adopted(self) -> None:
        block_id = self.block_id
        source_id = self._promote(
            self._candidate("https://example.com/arm", "有正文的页"),
            MemoryWebAdapter(),
        )
        proposed = propose_block_revision(
            self.repository, block_id, body="第一版给经理的稿。"
        )
        adopt_revision(self.repository, block_id, proposed["proposed_revision"]["version"])
        before = self._block(block_id)
        self.assertFalse(before["placeholder"])
        self.assertEqual(before["material_since_draft"], 0)
        attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="挂在收下之后的一段原话。",
            text="挂在收下之后的一段原话。",
        )
        after = self._block(block_id)
        self.assertEqual(after["material_since_draft"], 1)
        self.assertEqual(after["current_text"], "第一版给经理的稿。")
        self.assertTrue(
            all(
                item["verification_status"] == "captured"
                for item in self._claims()
            )
        )

    def test_placeholder_section_is_not_called_out_of_date(self) -> None:
        block_id = self.block_id
        source_id = self._promote(
            self._candidate("https://example.com/arm", "有正文的页"),
            MemoryWebAdapter(),
        )
        attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="挂在还没写的一节上。",
            text="挂在还没写的一节上。",
        )
        block = self._block(block_id)
        self.assertTrue(block["placeholder"])
        self.assertEqual(block["material_since_draft"], 0)

    def _block(self, block_id: str) -> dict:
        workbench = build_workbench_projection(self.repository, self.project_id)
        return next(
            item for item in workbench["blocks"] if item["id"] == block_id
        )

    def _claims(self) -> list[dict]:
        with self.repository.connect() as connection:
            rows = connection.execute(
                "SELECT verification_status FROM claims"
            ).fetchall()
        return [dict(row) for row in rows]

    def test_bulk_assign_over_http_writes_only_the_named_materials(self) -> None:
        picked = self._candidate("https://example.com/one", "勾上的")
        left_alone = self._candidate("https://example.com/two", "没勾的")
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        request = Request(
            server.origin
            + "/projects/"
            + self.project_id
            + "/materials/question",
            data=json.dumps(
                {"question_id": self.question_id, "candidate_ids": [picked]}
            ).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(status, 200)
        self.assertEqual(payload["candidate_ids"], [picked])
        tagged = {
            item["id"]: item["research_question_id"]
            for item in payload["workbench"]["materials"]["candidates"]
        }
        self.assertEqual(tagged[picked], self.question_id)
        self.assertIsNone(tagged[left_alone])


if __name__ == "__main__":
    unittest.main()
