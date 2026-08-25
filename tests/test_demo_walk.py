from __future__ import annotations

import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from app.adapters.sqlite_repository import SqliteRepository
from app.application.create_project import PLACEHOLDER_TEXT, create_project
from app.application.demo_walk import CLAIM_TEXT, DRAFT_BODY, run_blank_walk
from app.application.import_sample import import_sample
from app.cli import main
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class DemoWalkTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.output_dir = Path(self.temp_dir.name) / "walk"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()

    def test_walk_writes_draft_claim_and_word_without_inventing_facts(self) -> None:
        result = run_blank_walk(self.repository, self.output_dir)
        report = build_report_projection(self.repository, result["project_id"])
        review = build_review_context(self.repository, result["block_id"])
        claim = review["claims"][0]
        self.assertEqual(report["blocks"][0]["current_text"], DRAFT_BODY)
        self.assertNotIn(PLACEHOLDER_TEXT, report["blocks"][0]["current_text"])
        self.assertEqual(claim["text"], CLAIM_TEXT)
        self.assertEqual(claim["verification_status"], "source_checked")
        self.assertFalse(claim["independently_verified"])
        self.assertEqual(claim["provenance_scope"], "client_provided")
        self.assertTrue(Path(result["word_path"]).is_file())
        self.assertGreater(Path(result["word_path"]).stat().st_size, 0)
        self.assertIn("这不是网站", " ".join(result["said"]))

    def test_walk_does_not_rewrite_synthetic_sample(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        before = build_review_context(self.repository, "DB-001")
        before_status = {
            claim["id"]: (claim["verification_status"], claim["independently_verified"])
            for claim in before["claims"]
        }
        before_text = before["block"]["current_text"]
        run_blank_walk(self.repository, self.output_dir, name="另一题走查")
        after = build_review_context(self.repository, "DB-001")
        after_status = {
            claim["id"]: (claim["verification_status"], claim["independently_verified"])
            for claim in after["claims"]
        }
        self.assertEqual(after["block"]["current_text"], before_text)
        self.assertEqual(after_status, before_status)

    def test_list_projects_plain_prints_names_not_json(self) -> None:
        created = create_project(
            self.repository,
            name="口头走查题",
            original_context="客户只给了一句话。",
        )
        buf = StringIO()
        argv = [
            "app.cli",
            "--db",
            str(self.database_path),
            "list-projects",
            "--plain",
        ]
        with patch.object(sys, "argv", argv), patch("sys.stdout", buf):
            main()
        out = buf.getvalue()
        self.assertIn("这不是网站", out)
        self.assertIn("口头走查题", out)
        self.assertNotIn("{", out)
        self.assertNotIn(created["project_id"], out)


if __name__ == "__main__":
    unittest.main()
