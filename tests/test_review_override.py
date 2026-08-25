from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_post
from app.application.import_sample import import_sample
from app.application.create_project import PLACEHOLDER_TEXT, create_project
from app.application.review_block import (
    ReviewError,
    adopt_revision,
    propose_block_revision,
    record_override_decision,
    record_review_decision,
)
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ReviewOverrideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_approve_does_not_rewrite_draft_or_verification(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        before_status = _claim_status(self.repository, "C-002")
        result = record_review_decision(self.repository, "DB-001", action="approve")
        self.assertTrue(result["confirmation"]["recorded"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertIn("证据核验状态未改变", result["confirmation"]["message"])
        self.assertEqual(result["review_decision"]["id"], "RV-001")
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def test_modify_stores_candidate_without_replacing_current_text(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        result = record_review_decision(
            self.repository,
            "DB-001",
            action="modify",
            proposed_text="候选修改稿，尚未确认替换。",
        )
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(result["pending_revision"]["version"], 2)
        self.assertEqual(result["pending_revision"]["body"], "候选修改稿，尚未确认替换。")
        self.assertEqual(
            result["review_context"]["pending_revisions"][0]["body"],
            "候选修改稿，尚未确认替换。",
        )
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def test_adopt_replaces_current_text_only_after_confirm(self) -> None:
        record_review_decision(
            self.repository,
            "DB-001",
            action="modify",
            proposed_text="确认后的项目问题段落。",
        )
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = adopt_revision(self.repository, "DB-001", 2)
        self.assertFalse(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertEqual(_block_text(self.repository, "DB-001"), "确认后的项目问题段落。")
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        prior = result["review_context"]["prior_revision"]
        self.assertIsNotNone(prior)
        self.assertEqual(prior["version"], 1)
        self.assertEqual(prior["body"], before_text)
        report = build_report_projection(self.repository, self.project_id)
        self.assertEqual(report["blocks"][0]["current_text"], "确认后的项目问题段落。")
        self.assertEqual(report["blocks"][0]["current_version"], 2)

    def test_propose_keeps_placeholder_until_adopt(self) -> None:
        created = create_project(
            self.repository, name="可改稿题目", original_context="评估改造。"
        )
        block_id = created["report"]["blocks"][0]["id"]
        replacement = "租户结构仍不清楚，本轮只写缺口，不定改造必要性。"
        proposed = propose_block_revision(
            self.repository, block_id, body=replacement
        )
        self.assertEqual(_block_text(self.repository, block_id), PLACEHOLDER_TEXT)
        self.assertEqual(proposed["pending_revision"]["version"], 2)
        self.assertEqual(proposed["pending_revision"]["body"], replacement)
        self.assertTrue(proposed["confirmation"]["current_text_unchanged"])
        adopted = adopt_revision(self.repository, block_id, 2)
        self.assertEqual(_block_text(self.repository, block_id), replacement)
        self.assertFalse(adopted["confirmation"]["current_text_unchanged"])
        self.assertEqual(
            build_report_projection(self.repository, created["project_id"])["blocks"][0][
                "current_text"
            ],
            replacement,
        )

    def test_propose_does_not_change_synthetic_status_or_draft(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        before_status = _claim_status(self.repository, "C-002")
        result = propose_block_revision(
            self.repository,
            "DB-001",
            body="据客户提供，食品相关客群占比较高；是否构成风险仍待租户底表核验。",
        )
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(result["pending_revision"]["version"], 2)
        with self.assertRaisesRegex(ReviewError, "改稿正文"):
            propose_block_revision(self.repository, "DB-001", body="  ")
        with self.assertRaisesRegex(ReviewError, "相同"):
            propose_block_revision(self.repository, "DB-001", body=before_text)
        with self.assertRaisesRegex(ReviewError, "不存在"):
            propose_block_revision(self.repository, "DB-MISSING", body="一段改稿")

    def test_paragraph_override_keeps_sample_project_override_and_verification(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        result = record_override_decision(
            self.repository,
            deliverable_block_id="DB-001",
            handling="assumption",
        )
        self.assertEqual(result["override_decision"]["id"], "OVR-002")
        self.assertEqual(result["override_decision"]["deliverable_block_id"], "DB-001")
        self.assertEqual(result["confirmation"]["treatment"], "按假设推进")
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")
        context = build_review_context(self.repository, "DB-001")
        self.assertEqual(context["project_override"]["id"], "OVR-001")
        self.assertEqual(context["latest_override"]["id"], "OVR-002")

    def test_project_override_does_not_change_verification(self) -> None:
        result = record_override_decision(
            self.repository,
            project_id=self.project_id,
            handling="scenario",
            reason="售前时间不足，按情景表达",
        )
        self.assertEqual(result["override_decision"]["id"], "OVR-002")
        self.assertIsNone(result["override_decision"]["deliverable_block_id"])
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def test_invalid_action_and_missing_block_are_rejected(self) -> None:
        with self.assertRaises(ReviewError):
            record_review_decision(self.repository, "DB-001", action="publish")
        with self.assertRaises(ReviewError):
            record_review_decision(self.repository, "DB-missing", action="approve")


class ReviewOverrideHttpTest(unittest.TestCase):
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

    def test_http_review_matches_application_and_keeps_object_ids(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/deliverable-blocks/DB-001/review-decisions",
            {"action": "approve"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["review_context"]["block"]["id"], "DB-001")
        claim = next(
            item for item in payload["review_context"]["claims"] if item["id"] == "C-002"
        )
        self.assertEqual(claim["verification_status"], "captured")
        self.assertEqual(claim["independently_verified"], False)
        dispatched = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-002/review-decisions",
            {"action": "exclude"},
        )
        self.assertEqual(dispatched[0], 201)
        self.assertIn("从本版排除", dispatched[1]["confirmation"]["message"])

    def test_http_override_and_adopt(self) -> None:
        override_status, override_payload = _http_json(
            "POST",
            self.server.origin + "/deliverable-blocks/DB-001/override-decisions",
            {
                "handling": "scenario",
                "proposed_text": "按情景表达的项目问题候选。",
            },
        )
        self.assertEqual(override_status, 201)
        self.assertTrue(override_payload["confirmation"]["current_text_unchanged"])
        self.assertIn("生产型租户占比可以作为客户提供信息进入报告", _block_text(self.repository, "DB-001"))
        adopt_status, adopt_payload = _http_json(
            "POST",
            self.server.origin + "/deliverable-blocks/DB-001/revisions/adopt",
            {"version": 2},
        )
        self.assertEqual(adopt_status, 200)
        self.assertEqual(_block_text(self.repository, "DB-001"), "按情景表达的项目问题候选。")
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")
        self.assertFalse(adopt_payload["confirmation"]["current_text_unchanged"])

    def test_http_propose_then_adopt(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/deliverable-blocks/DB-004/revisions",
            {"body": "下一动作仍是要租户、面积和租金底表，不是已核实结论。"},
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["confirmation"]["current_text_unchanged"])
        self.assertIn("脱敏租户", _block_text(self.repository, "DB-004"))
        adopt_status, adopt_payload = _http_json(
            "POST",
            self.server.origin + "/deliverable-blocks/DB-004/revisions/adopt",
            {"version": 2},
        )
        self.assertEqual(adopt_status, 200)
        self.assertEqual(
            _block_text(self.repository, "DB-004"),
            "下一动作仍是要租户、面积和租金底表，不是已核实结论。",
        )
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")
        empty = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-001/revisions",
            {"body": ""},
        )
        self.assertEqual(empty[0], 400)
        missing = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-MISSING/revisions",
            {"body": "改稿"},
        )
        self.assertEqual(missing[0], 404)


def _block_text(repository: SqliteRepository, block_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()["current_text"]


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
