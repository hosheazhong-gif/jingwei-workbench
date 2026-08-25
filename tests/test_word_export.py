from __future__ import annotations

import base64
import io
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_post
from app.application.create_project import create_project
from app.application.export_deliverable import export_project
from app.application.import_sample import import_sample
from app.application.review_block import adopt_revision, propose_block_revision, record_review_decision
from app.exporters.word import WordInternalDraftExporter
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class WordExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_word_uses_current_draft_and_same_block_ids(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        markdown = export_project(self.repository, self.project_id, "markdown")
        result = export_project(self.repository, self.project_id, "word")
        text = _docx_text(_decode_export(result))
        self.assertEqual(result["exporter_key"], "word")
        self.assertEqual(result["content_encoding"], "base64")
        self.assertEqual(result["block_ids"], markdown["block_ids"])
        self.assertTrue(result["filename"].endswith(".docx"))
        self.assertIn("word/document.xml", zipfile.ZipFile(io.BytesIO(_decode_export(result))).namelist())
        self.assertIn(report["project"]["name"], text)
        self.assertIn("项目问题", text)
        self.assertIn(report["blocks"][0]["current_text"].splitlines()[0], text)
        self.assertIn("据客户提供", text)
        self.assertIn("未独立核实", text)
        # 来源只给名称加链接／文件名，主张全文不再复述进导出
        self.assertIn("来源：", text)
        self.assertNotIn("口径与来源", text)
        self.assertIn("没有重新生成事实", text)
        self.assertIn("证据核验状态未改变", text)
        self.assertEqual(result["confirmation"]["record_kind"], "export")
        self.assertEqual(result["confirmation"]["exporter_key"], "word")
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])

    def test_word_does_not_change_markdown_or_verification(self) -> None:
        before_markdown = export_project(self.repository, self.project_id, "markdown")
        before_status = _claim_status(self.repository, "C-002")
        before = build_report_projection(self.repository, self.project_id)
        export_project(self.repository, self.project_id, "word")
        after_markdown = export_project(self.repository, self.project_id, "markdown")
        after = build_report_projection(self.repository, self.project_id)
        self.assertEqual(before_markdown["content"], after_markdown["content"])
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(
            [block["current_text"] for block in before["blocks"]],
            [block["current_text"] for block in after["blocks"]],
        )

    def test_excluded_block_and_candidate_are_omitted(self) -> None:
        record_review_decision(self.repository, "DB-003", action="exclude")
        record_review_decision(
            self.repository,
            "DB-001",
            action="modify",
            proposed_text="不应出现在导出中的候选正文XYZ",
        )
        result = export_project(self.repository, self.project_id, "word")
        text = _docx_text(_decode_export(result))
        self.assertEqual(result["block_ids"], ["DB-001", "DB-002", "DB-004"])
        self.assertIn("候选方向", result["omitted_titles"])
        self.assertIn("未进入本版的段落", text)
        self.assertNotIn("它们是研究假设，不是推荐方案", text)
        self.assertNotIn("不应出现在导出中的候选正文XYZ", text)
        self.assertIn("生产型租户占比可以作为客户提供信息进入报告", text)

    def test_word_exporter_source_has_no_synthetic_terms(self) -> None:
        source = (PROJECT_ROOT / "app/exporters/word.py").read_text(encoding="utf-8")
        for term in ("远川园区", "冷链", "星河优选", "生产型租户"):
            self.assertNotIn(term, source)

    def test_same_report_has_reproducible_docx_bytes(self) -> None:
        first = export_project(self.repository, self.project_id, "word")
        second = export_project(self.repository, self.project_id, "word")
        self.assertEqual(first["content"], second["content"])
        with zipfile.ZipFile(io.BytesIO(_decode_export(first))) as archive:
            self.assertTrue(
                all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
            )

    def test_unsourced_number_is_red_in_word_without_changing_verification(self) -> None:
        created = create_project(
            self.repository,
            name="缺口导出",
            original_context="还值不值得跟。",
        )
        block_id = created["report"]["blocks"][0]["id"]
        proposed = propose_block_revision(
            self.repository,
            block_id,
            body="缺口稿：产值约 6000亿，口径待补。",
        )
        adopt_revision(
            self.repository, block_id, proposed["pending_revision"]["version"]
        )
        before = _claim_status(self.repository, "C-002")
        result = export_project(self.repository, created["project_id"], "word")
        xml = zipfile.ZipFile(io.BytesIO(_decode_export(result))).read(
            "word/document.xml"
        ).decode("utf-8")
        self.assertIn("6000亿", xml)
        self.assertIn("w:color", xml)
        self.assertIn("9B2C2C", xml)
        self.assertEqual(_claim_status(self.repository, "C-002"), before)
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])

    def test_word_splits_sentence_wall_and_keeps_existing_lines(self) -> None:
        xml = zipfile.ZipFile(
            io.BytesIO(
                WordInternalDraftExporter().export(
                    [
                        {
                            "project_name": "分段导出",
                            "title": "缺口",
                            "current_text": "第一句。第二句。第三句。第四句。",
                        },
                        {
                            "title": "客户资料请求",
                            "current_text": "1. 先补租户。\n2. 再补仓容。",
                        },
                    ]
                )
            )
        ).read("word/document.xml").decode("utf-8")
        self.assertIn(">第一句。<", xml)
        self.assertIn(">第二句。<", xml)
        self.assertIn(">第三句。<", xml)
        self.assertIn(">第四句。<", xml)
        self.assertIn(">1. 先补租户。<", xml)
        self.assertIn(">2. 再补仓容。<", xml)


class WordExportHttpTest(unittest.TestCase):
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
            self.server.origin + "/projects/P-DEMO-001/exports/word",
            {},
        )
        dispatched = dispatch_post(
            self.repository, "/projects/P-DEMO-001/exports/word", {}
        )
        application = export_project(self.repository, "P-DEMO-001", "word")
        self.assertEqual(status, 200)
        self.assertEqual(dispatched, (200, application))
        self.assertEqual(payload["content"], application["content"])
        self.assertEqual(payload["block_ids"], application["block_ids"])
        self.assertEqual(_docx_text(_decode_export(payload)), _docx_text(_decode_export(application)))


def _decode_export(result: dict) -> bytes:
    return base64.b64decode(result["content"])


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return re.sub(r"<[^>]+>", "", xml)


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
