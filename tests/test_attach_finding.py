from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_post
from app.application.attach_claim import attach_claim_to_block
from app.application.attach_finding import FindingAttachError, attach_finding_to_block
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class AttachFindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.incoming = Path(self.temp_dir.name) / "note.txt"
        self.incoming.write_text("租户结构仍待现场核对。\n", encoding="utf-8")

    def test_attaches_finding_with_supporting_claim(self) -> None:
        created = create_project(
            self.repository, name="可挂判断的题目", original_context="评估改造。"
        )
        block_id = created["report"]["blocks"][0]["id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        source_id = capture_local_source(
            self.repository, created["project_id"], self.incoming, title="现场笔记"
        )["source"]["id"]
        claim_id = attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="租户结构仍待现场核对。",
            text="目前没有独立核实的租户结构。",
            epistemic_type="factual_claim",
        )["claim_id"]
        result = attach_finding_to_block(
            self.repository,
            block_id,
            text="现有材料只支持缺口判断，不能定论改造必要性。",
            claim_ids=[claim_id],
            alternative="也可能只是材料尚未到齐。",
            confidence="low",
        )
        finding = result["review_context"]["findings"][0]
        self.assertEqual(result["finding_id"], "F-001")
        self.assertEqual(finding["text"], "现有材料只支持缺口判断，不能定论改造必要性。")
        self.assertEqual(finding["confidence_label"], "弱")
        self.assertEqual(finding["supporting_claims"][0]["id"], claim_id)
        self.assertEqual(finding["alternatives"], ["也可能只是材料尚未到齐。"])
        self.assertEqual(result["review_context"]["block"]["current_text"], placeholder)
        self.assertEqual(_claim_status(self.repository, claim_id), "captured")
        self.assertIn(result["finding_id"], result["report"]["blocks"][0]["finding_ids"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])

    def test_does_not_change_synthetic_status_or_draft(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-002")
        before_findings = build_review_context(self.repository, "DB-002")["findings"]
        result = attach_finding_to_block(
            self.repository,
            "DB-002",
            text="案例只能提供方向，不能证明适配本项目。",
            claim_ids=["C-008"],
            confidence="low",
        )
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-002"), before_text)
        self.assertEqual(result["finding_id"], "F-004")
        after = build_review_context(self.repository, "DB-002")["findings"]
        self.assertEqual(len(before_findings), 1)
        self.assertEqual(before_findings[0]["id"], "F-002")
        self.assertEqual(len(after), 2)
        self.assertEqual(len(build_report_projection(self.repository, "P-DEMO-001")["blocks"]), 4)

    def test_review_context_reads_existing_synthetic_finding(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        review = build_review_context(self.repository, "DB-002")
        self.assertEqual(review["findings"][0]["id"], "F-002")
        self.assertIn("不能证明适配远川园区", review["findings"][0]["text"])
        self.assertEqual(review["findings"][0]["confidence_label"], "弱")

    def test_rejects_empty_text_and_foreign_claim(self) -> None:
        first = create_project(self.repository, name="甲题", original_context="甲。")
        second = create_project(self.repository, name="乙题", original_context="乙。")
        source_id = capture_local_source(
            self.repository, first["project_id"], self.incoming, title="甲材料"
        )["source"]["id"]
        claim_id = attach_claim_to_block(
            self.repository,
            first["report"]["blocks"][0]["id"],
            source_id=source_id,
            excerpt="摘录",
            text="主张",
            epistemic_type="factual_claim",
        )["claim_id"]
        with self.assertRaisesRegex(FindingAttachError, "判断"):
            attach_finding_to_block(
                self.repository,
                first["report"]["blocks"][0]["id"],
                text="  ",
            )
        with self.assertRaisesRegex(FindingAttachError, "不属于当前题目"):
            attach_finding_to_block(
                self.repository,
                second["report"]["blocks"][0]["id"],
                text="乙题判断",
                claim_ids=[claim_id],
            )


class AttachFindingHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_attaches_finding(self) -> None:
        created_status, created = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "HTTP 判断题", "original_context": "建题后挂判断。"},
        )
        self.assertEqual(created_status, 201)
        block_id = created["report"]["blocks"][0]["id"]
        status, payload = _http_json(
            "POST",
            self.server.origin + f"/deliverable-blocks/{block_id}/findings",
            {
                "text": "当前只能写缺口，不能写成已核实需求。",
                "confidence": "low",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["review_context"]["findings"][0]["confidence_label"], "弱")
        dispatched = dispatch_post(
            self.repository,
            f"/deliverable-blocks/{block_id}/findings",
            {"text": "", "confidence": "low"},
        )
        self.assertEqual(dispatched[0], 400)
        missing = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-MISSING/findings",
            {"text": "判断"},
        )
        self.assertEqual(missing[0], 404)


def _claim_status(repository: SqliteRepository, claim_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT verification_status FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()["verification_status"]


def _block_text(repository: SqliteRepository, block_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()["current_text"]


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
