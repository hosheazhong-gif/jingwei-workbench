from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_post
from app.application.export_deliverable import ExportError, export_project
from app.application.import_sample import import_sample
from app.application.review_block import record_review_decision
from app.exporters import default_exporters
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class MarkdownExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_markdown_uses_current_draft_and_same_block_ids(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        result = export_project(self.repository, self.project_id, "markdown")
        self.assertEqual(result["exporter_key"], "markdown")
        self.assertEqual(result["block_ids"], ["DB-001", "DB-002", "DB-003", "DB-004"])
        self.assertTrue(result["filename"].endswith(".md"))
        self.assertIn(report["project"]["name"], result["content"])
        self.assertIn("项目问题", result["content"])
        self.assertIn(report["blocks"][0]["current_text"], result["content"])
        self.assertIn("据客户提供", result["content"])
        self.assertIn("未独立核实", result["content"])
        self.assertIn("来源：", result["content"])
        self.assertNotIn("口径与来源", result["content"])
        self.assertIn("没有重新生成事实", result["content"])
        self.assertIn("证据核验状态未改变", result["content"])
        self.assertEqual(result["confirmation"]["record_kind"], "export")
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertEqual(result["confirmation"]["block_count"], 4)
        self.assertIn("未改变主张核验", result["confirmation"]["message"])

    def test_export_does_not_change_verification_or_current_text(self) -> None:
        before = build_report_projection(self.repository, self.project_id)
        before_status = _claim_status(self.repository, "C-002")
        export_project(self.repository, self.project_id, "markdown")
        after = build_report_projection(self.repository, self.project_id)
        self.assertEqual(before_status, "captured")
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(
            [block["current_text"] for block in before["blocks"]],
            [block["current_text"] for block in after["blocks"]],
        )

    def test_excluded_block_is_omitted_and_candidate_is_not_exported(self) -> None:
        record_review_decision(self.repository, "DB-003", action="exclude")
        record_review_decision(
            self.repository,
            "DB-001",
            action="modify",
            proposed_text="不应出现在导出中的候选正文XYZ",
        )
        result = export_project(self.repository, self.project_id, "markdown")
        self.assertEqual(result["block_ids"], ["DB-001", "DB-002", "DB-004"])
        self.assertIn("候选方向", result["omitted_titles"])
        self.assertIn("未进入本版的段落", result["content"])
        self.assertNotIn("它们是研究假设，不是推荐方案", result["content"])
        self.assertNotIn("不应出现在导出中的候选正文XYZ", result["content"])
        self.assertIn("生产型租户占比可以作为客户提供信息进入报告", result["content"])

    def test_second_exporter_does_not_change_markdown(self) -> None:
        class DummyExporter:
            key = "plain"

            def export(self, approved_blocks):
                return b"dummy-plain"

        before = export_project(self.repository, self.project_id, "markdown")
        registry = default_exporters()
        registry["plain"] = DummyExporter()
        after = export_project(
            self.repository, self.project_id, "markdown", exporters=registry
        )
        dummy = export_project(
            self.repository, self.project_id, "plain", exporters=registry
        )
        self.assertEqual(before["content"], after["content"])
        self.assertEqual(dummy["content"], "dummy-plain")

    def test_unknown_exporter_and_project_are_rejected(self) -> None:
        with self.assertRaises(ExportError):
            export_project(self.repository, self.project_id, "pptx")
        with self.assertRaises(ExportError):
            export_project(self.repository, "missing", "markdown")


class MarkdownExportHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        import_sample(self.repository, SAMPLE_PATH)
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_matches_application(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects/P-DEMO-001/exports/markdown",
            {},
        )
        dispatched = dispatch_post(
            self.repository, "/projects/P-DEMO-001/exports/markdown", {}
        )
        application = export_project(self.repository, "P-DEMO-001", "markdown")
        self.assertEqual(status, 200)
        self.assertEqual(dispatched, (200, application))
        self.assertEqual(payload["content"], application["content"])
        self.assertEqual(payload["block_ids"], ["DB-001", "DB-002", "DB-003", "DB-004"])

    def test_unknown_exporter_is_404(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects/P-DEMO-001/exports/pptx",
            {},
        )
        self.assertEqual(status, 404)
        self.assertIn("error", payload)


def _claim_status(repository: SqliteRepository, claim_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT verification_status FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()["verification_status"]


def _http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, method=method, headers=headers)
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, body


if __name__ == "__main__":
    unittest.main()
