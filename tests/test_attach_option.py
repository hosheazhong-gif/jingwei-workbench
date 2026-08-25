from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_post
from app.application.attach_option import OptionAttachError, attach_option_to_block
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class AttachOptionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()

    def test_attaches_candidate_option_without_rewriting_draft(self) -> None:
        created = create_project(
            self.repository, name="可挂方向的题目", original_context="评估改造。"
        )
        block_id = created["report"]["blocks"][0]["id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        result = attach_option_to_block(
            self.repository,
            block_id,
            text="先补租户结构，再讨论改造必要性。",
        )
        option = result["review_context"]["options"][0]
        self.assertEqual(result["option_id"], "O-001")
        self.assertEqual(option["text"], "先补租户结构，再讨论改造必要性。")
        self.assertEqual(option["status"], "candidate")
        self.assertEqual(option["status_label"], "待验证")
        self.assertEqual(result["review_context"]["block"]["current_text"], placeholder)
        self.assertIn(result["option_id"], result["report"]["blocks"][0]["option_ids"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])

    def test_does_not_change_synthetic_status_or_draft(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-003")
        before_options = build_review_context(self.repository, "DB-003")["options"]
        result = attach_option_to_block(
            self.repository,
            "DB-003",
            text="先核对空间合规，再比较三类方向。",
            status="needs_evidence",
        )
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-003"), before_text)
        self.assertEqual(result["option_id"], "O-004")
        after = build_review_context(self.repository, "DB-003")["options"]
        self.assertEqual(len(before_options), 3)
        self.assertEqual([item["id"] for item in before_options], ["O-001", "O-002", "O-003"])
        self.assertEqual(len(after), 4)
        self.assertEqual(after[-1]["status_label"], "需补证")
        self.assertEqual(len(build_report_projection(self.repository, "P-DEMO-001")["blocks"]), 4)

    def test_review_context_reads_existing_synthetic_options(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        review = build_review_context(self.repository, "DB-003")
        self.assertEqual([item["id"] for item in review["options"]], ["O-001", "O-002", "O-003"])
        self.assertIn("共享生产", review["options"][0]["text"])
        self.assertEqual(review["options"][0]["status_label"], "待验证")
        empty = build_review_context(self.repository, "DB-001")
        self.assertEqual(empty["options"], [])

    def test_rejects_empty_text_and_unknown_status(self) -> None:
        created = create_project(self.repository, name="甲题", original_context="甲。")
        block_id = created["report"]["blocks"][0]["id"]
        with self.assertRaisesRegex(OptionAttachError, "方向"):
            attach_option_to_block(self.repository, block_id, text="  ")
        with self.assertRaisesRegex(OptionAttachError, "方向状态"):
            attach_option_to_block(
                self.repository,
                block_id,
                text="一条方向",
                status="hypothesis",
            )
        with self.assertRaisesRegex(OptionAttachError, "不存在"):
            attach_option_to_block(
                self.repository,
                "DB-MISSING",
                text="一条方向",
            )


class AttachOptionHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_attaches_option(self) -> None:
        created_status, created = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "HTTP 方向题", "original_context": "建题后挂方向。"},
        )
        self.assertEqual(created_status, 201)
        block_id = created["report"]["blocks"][0]["id"]
        status, payload = _http_json(
            "POST",
            self.server.origin + f"/deliverable-blocks/{block_id}/options",
            {"text": "先补材料，再比较方向。"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["review_context"]["options"][0]["status_label"], "待验证")
        dispatched = dispatch_post(
            self.repository,
            f"/deliverable-blocks/{block_id}/options",
            {"text": "", "status": "candidate"},
        )
        self.assertEqual(dispatched[0], 400)
        missing = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-MISSING/options",
            {"text": "方向"},
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
