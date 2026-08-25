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
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.application.review_block import record_review_decision
from app.application.verify_claim import ClaimVerifyError, update_claim_verification


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class VerifyClaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()

    def test_updates_status_without_rewriting_draft_or_independence(self) -> None:
        created = create_project(
            self.repository, name="可核验题目", original_context="评估改造。"
        )
        block_id = created["report"]["blocks"][0]["id"]
        incoming = Path(self.temp_dir.name) / "note.txt"
        incoming.write_text("租户结构仍待现场核对。\n", encoding="utf-8")
        source_id = capture_local_source(
            self.repository, created["project_id"], incoming, title="现场笔记"
        )["source"]["id"]
        claim_id = attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="租户结构仍待现场核对。",
            text="目前没有独立核实的租户结构。",
            epistemic_type="factual_claim",
        )["claim_id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        result = update_claim_verification(
            self.repository,
            block_id,
            claim_id,
            verification_status="source_checked",
        )
        claim = result["review_context"]["claims"][0]
        self.assertEqual(claim["verification_status"], "source_checked")
        self.assertIs(claim["independently_verified"], False)
        self.assertEqual(result["review_context"]["block"]["current_text"], placeholder)
        self.assertFalse(result["confirmation"]["verification_status_unchanged"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["independently_verified_unchanged"])

    def test_synthetic_client_claim_can_be_checked_but_not_independently_verified(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        before_text = _block_text(self.repository, "DB-001")
        before_other = _claim_status(self.repository, "C-001")
        result = update_claim_verification(
            self.repository,
            "DB-001",
            "C-002",
            verification_status="corroborated",
        )
        claim = next(
            item for item in result["review_context"]["claims"] if item["id"] == "C-002"
        )
        self.assertEqual(claim["verification_status"], "corroborated")
        self.assertIs(claim["independently_verified"], False)
        self.assertEqual(claim["provenance_scope"], "client_provided")
        self.assertIn("据客户提供", claim["delivery_rule"])
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-001"), before_other)
        review = record_review_decision(self.repository, "DB-001", action="approve")
        self.assertEqual(_claim_status(self.repository, "C-002"), "corroborated")
        self.assertTrue(review["confirmation"]["verification_status_unchanged"])

    def test_rejects_same_status_foreign_and_missing(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        created = create_project(self.repository, name="乙题", original_context="乙。")
        with self.assertRaisesRegex(ClaimVerifyError, "没有变化"):
            update_claim_verification(
                self.repository,
                "DB-001",
                "C-002",
                verification_status="captured",
            )
        with self.assertRaisesRegex(ClaimVerifyError, "核验状态"):
            update_claim_verification(
                self.repository,
                "DB-001",
                "C-002",
                verification_status="verified_fact",
            )
        with self.assertRaisesRegex(ClaimVerifyError, "未挂到当前段落"):
            update_claim_verification(
                self.repository,
                "DB-002",
                "C-002",
                verification_status="source_checked",
            )
        with self.assertRaisesRegex(ClaimVerifyError, "不属于当前题目"):
            update_claim_verification(
                self.repository,
                created["report"]["blocks"][0]["id"],
                "C-002",
                verification_status="source_checked",
            )
        with self.assertRaisesRegex(ClaimVerifyError, "不存在"):
            update_claim_verification(
                self.repository,
                "DB-001",
                "C-MISSING",
                verification_status="source_checked",
            )


class VerifyClaimHttpTest(unittest.TestCase):
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

    def test_http_updates_verification(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/deliverable-blocks/DB-001/claims/C-002/verification",
            {"verification_status": "source_checked"},
        )
        self.assertEqual(status, 200)
        claim = next(
            item for item in payload["review_context"]["claims"] if item["id"] == "C-002"
        )
        self.assertEqual(claim["verification_status"], "source_checked")
        self.assertIs(claim["independently_verified"], False)
        self.assertFalse(payload["confirmation"]["verification_status_unchanged"])
        same = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-001/claims/C-002/verification",
            {"verification_status": "source_checked"},
        )
        self.assertEqual(same[0], 400)
        missing = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-001/claims/C-MISSING/verification",
            {"verification_status": "source_checked"},
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
