from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_post
from app.application.attach_claim import (
    ClaimAttachError,
    attach_claim_to_block,
    unlink_claim_from_block,
)
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class AttachClaimTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.incoming = Path(self.temp_dir.name) / "note.txt"
        self.incoming.write_text("租户结构仍待现场核对。\n", encoding="utf-8")

    def test_attaches_excerpt_and_claim_without_rewriting_draft(self) -> None:
        created = create_project(
            self.repository,
            name="可挂接主张的题目",
            original_context="评估冷库改造。",
        )
        project_id = created["project_id"]
        block_id = created["report"]["blocks"][0]["id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        source_id = capture_local_source(
            self.repository, project_id, self.incoming, title="现场笔记"
        )["source"]["id"]
        result = attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="租户结构仍待现场核对。",
            text="目前没有独立核实的租户结构。",
            epistemic_type="factual_claim",
            locator="第1段",
            context_limit="缺少租户名单和时点",
        )
        review = result["review_context"]
        claim = review["claims"][0]
        evidence = claim["evidence"][0]
        self.assertEqual(result["claim_id"], "C-001")
        self.assertEqual(result["excerpt_id"], "E-001")
        self.assertEqual(claim["text"], "目前没有独立核实的租户结构。")
        self.assertEqual(claim["epistemic_type"], "factual_claim")
        self.assertEqual(claim["verification_status"], "captured")
        self.assertIs(claim["independently_verified"], False)
        self.assertIn("尚未核验", claim["delivery_rule"])
        self.assertEqual(claim["source"]["title"], "现场笔记")
        self.assertEqual(evidence["excerpt"], "租户结构仍待现场核对。")
        self.assertEqual(evidence["locator"]["kind"], "manual")
        self.assertEqual(evidence["context_limit"], "缺少租户名单和时点")
        self.assertEqual(review["block"]["current_text"], placeholder)
        self.assertIn("已挂接主张", review["block"]["restriction"])
        self.assertIn(result["claim_id"], result["report"]["blocks"][0]["claim_ids"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])

    def test_excerpt_only_defaults_claim_text_and_type(self) -> None:
        created = create_project(
            self.repository,
            name="只挂原话",
            original_context="评估冷库改造。",
        )
        block_id = created["report"]["blocks"][0]["id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        source_id = capture_local_source(
            self.repository, created["project_id"], self.incoming, title="现场笔记"
        )["source"]["id"]
        result = attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="租户结构仍待现场核对。",
        )
        claim = result["review_context"]["claims"][0]
        self.assertEqual(claim["text"], "租户结构仍待现场核对。")
        self.assertEqual(claim["epistemic_type"], "factual_claim")
        self.assertEqual(claim["verification_status"], "captured")
        self.assertEqual(result["review_context"]["block"]["current_text"], placeholder)

    def test_unlink_keeps_claim_and_draft(self) -> None:
        created = create_project(
            self.repository,
            name="可拿掉原话",
            original_context="评估冷库改造。",
        )
        block_id = created["report"]["blocks"][0]["id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        source_id = capture_local_source(
            self.repository, created["project_id"], self.incoming, title="现场笔记"
        )["source"]["id"]
        attached = attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="租户结构仍待现场核对。",
        )
        result = unlink_claim_from_block(
            self.repository, block_id, attached["claim_id"]
        )
        review = build_review_context(self.repository, block_id)
        self.assertEqual(review["claims"], [])
        self.assertEqual(review["block"]["current_text"], placeholder)
        self.assertEqual(
            _claim_status(self.repository, attached["claim_id"]), "captured"
        )
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        created = create_project(
            self.repository, name="口径题", original_context="一句任务。"
        )
        block_id = created["report"]["blocks"][0]["id"]
        source_id = capture_local_source(
            self.repository, created["project_id"], self.incoming, title="客户口头"
        )["source"]["id"]
        result = attach_claim_to_block(
            self.repository,
            block_id,
            source_id=source_id,
            excerpt="客群占比超过六成。",
            text="客户称生产型租户占比超过60%。",
            epistemic_type="factual_claim",
            provenance_scope="client_provided",
            macro_market=True,
        )
        claim = result["review_context"]["claims"][0]
        self.assertEqual(claim["provenance_scope"], "client_provided")
        self.assertIs(claim["independently_verified"], False)
        self.assertEqual(claim["verification_status"], "captured")
        self.assertIn("据客户提供", claim["delivery_rule"])
        self.assertIn("不等于外部独立核实", claim["delivery_rule"])
        self.assertIn("不单独证明项目需求", claim["delivery_rule"])

    def test_does_not_change_synthetic_status_or_draft(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = attach_claim_to_block(
            self.repository,
            "DB-001",
            source_id="S-007",
            excerpt="宏观市场表格待核验。",
            text="宏观产值数据不能单独证明本项目需求。",
            epistemic_type="inference",
        )
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, result["claim_id"]), "captured")
        new_claim = next(
            item
            for item in build_review_context(self.repository, "DB-001")["claims"]
            if item["id"] == result["claim_id"]
        )
        self.assertIn("不单独证明项目需求", new_claim["delivery_rule"])
        self.assertEqual(len(build_report_projection(self.repository, "P-DEMO-001")["blocks"]), 4)

    def test_rejects_empty_fields_and_foreign_source(self) -> None:
        first = create_project(
            self.repository, name="甲题", original_context="甲。"
        )
        second = create_project(
            self.repository, name="乙题", original_context="乙。"
        )
        source_id = capture_local_source(
            self.repository, first["project_id"], self.incoming, title="甲材料"
        )["source"]["id"]
        with self.assertRaisesRegex(ClaimAttachError, "摘录"):
            attach_claim_to_block(
                self.repository,
                first["report"]["blocks"][0]["id"],
                source_id=source_id,
                excerpt="  ",
                text="主张",
                epistemic_type="factual_claim",
            )
        with self.assertRaisesRegex(ClaimAttachError, "认识类型"):
            attach_claim_to_block(
                self.repository,
                first["report"]["blocks"][0]["id"],
                source_id=source_id,
                excerpt="摘录",
                text="主张",
                epistemic_type="hypothesis",
            )
        with self.assertRaisesRegex(ClaimAttachError, "不属于当前题目"):
            attach_claim_to_block(
                self.repository,
                second["report"]["blocks"][0]["id"],
                source_id=source_id,
                excerpt="摘录",
                text="主张",
                epistemic_type="factual_claim",
            )
        with self.assertRaisesRegex(ClaimAttachError, "不存在"):
            attach_claim_to_block(
                self.repository,
                "DB-MISSING",
                source_id=source_id,
                excerpt="摘录",
                text="主张",
                epistemic_type="factual_claim",
            )


class AttachClaimHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.incoming = Path(self.temp_dir.name) / "note.txt"
        self.incoming.write_text("现场待核。\n", encoding="utf-8")
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_attaches_and_ignores_forged_verification(self) -> None:
        created_status, created = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "HTTP 主张题", "original_context": "建题后挂主张。"},
        )
        self.assertEqual(created_status, 201)
        project_id = created["project_id"]
        block_id = created["report"]["blocks"][0]["id"]
        source_id = capture_local_source(
            self.repository, project_id, self.incoming, title="HTTP材料"
        )["source"]["id"]
        status, payload = _http_json(
            "POST",
            self.server.origin + f"/deliverable-blocks/{block_id}/claims",
            {
                "source_id": source_id,
                "excerpt": "现场待核。",
                "text": "现场情况尚未核实。",
                "epistemic_type": "factual_claim",
                "verification_status": "corroborated",
                "independently_verified": True,
            },
        )
        self.assertEqual(status, 201)
        claim = payload["review_context"]["claims"][0]
        self.assertEqual(claim["verification_status"], "captured")
        self.assertIs(claim["independently_verified"], False)
        unlinked = _http_json(
            "POST",
            self.server.origin
            + f"/deliverable-blocks/{block_id}/claims/{payload['claim_id']}/unlink",
            {},
        )
        self.assertEqual(unlinked[0], 200)
        self.assertTrue(unlinked[1]["confirmation"]["current_text_unchanged"])
        self.assertNotIn(
            payload["claim_id"],
            [
                item["claim_id"]
                for item in unlinked[1]["workbench"]["blocks"][0]["claim_sources"]
            ],
        )
        excerpt_only = _http_json(
            "POST",
            self.server.origin + f"/deliverable-blocks/{block_id}/claims",
            {"source_id": source_id, "excerpt": "只需原话。"},
        )
        self.assertEqual(excerpt_only[0], 201)
        self.assertEqual(
            excerpt_only[1]["review_context"]["claims"][-1]["text"],
            "只需原话。",
        )
        dispatched = dispatch_post(
            self.repository,
            f"/deliverable-blocks/{block_id}/claims",
            {"source_id": source_id, "excerpt": "", "text": "主张", "epistemic_type": "factual_claim"},
        )
        self.assertEqual(dispatched[0], 400)
        missing = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-MISSING/claims",
            {
                "source_id": source_id,
                "excerpt": "摘录",
                "text": "主张",
                "epistemic_type": "factual_claim",
            },
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
