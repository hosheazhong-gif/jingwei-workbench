from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_get, dispatch_post
from app.application.import_sample import import_sample
from app.application.update_brief import BriefUpdateError, update_brief
from app.projections.brief import build_brief_projection
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class BriefViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_projection_reads_sample_ids_and_natural_language(self) -> None:
        projection = build_brief_projection(self.repository, self.project_id)
        self.assertEqual(projection["brief"]["id"], "B-DEMO-001")
        self.assertEqual(projection["project"]["decision_gate_label"], "可继续头脑风暴")
        self.assertTrue(projection["brief"]["not_a_final_client_recommendation"])
        self.assertEqual(
            [item["id"] for item in projection["questions"]],
            ["RQ-01", "RQ-02", "RQ-04", "RQ-06"],
        )
        self.assertTrue(all(item["question"].strip() for item in projection["questions"]))
        self.assertTrue(all(item["enough_for_now"] for item in projection["questions"]))
        self.assertEqual(projection["questions"][0]["status_label"], "未开始")
        dumped = json.dumps(projection, ensure_ascii=False)
        self.assertNotIn("行业前景与市场表现", dumped)
        self.assertIn("不是画布或看板", projection["limitation"])

    def test_brief_view_does_not_copy_report_conclusions(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        brief = build_brief_projection(self.repository, self.project_id)
        self.assertEqual(brief["project"]["id"], report["project"]["id"])
        self.assertNotEqual(
            {block["id"] for block in report["blocks"]},
            {item["id"] for item in brief["questions"]},
        )
        self.assertNotIn(
            "生产型租户占比可以作为客户提供信息进入报告",
            json.dumps(brief, ensure_ascii=False),
        )

    def test_update_does_not_change_verification_or_draft(self) -> None:
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = update_brief(
            self.repository,
            self.project_id,
            name="改名后的远川园区研究",
            decision_question="更新后的决策问题，仅用于任务边界。",
            questions=[
                {
                    "id": "RQ-02",
                    "status": "waiting_for_material",
                    "enough_for_now": "能回到分母和时点",
                }
            ],
        )
        projection = result["brief_projection"]
        self.assertEqual(projection["project"]["name"], "改名后的远川园区研究")
        self.assertEqual(projection["brief"]["decision_question"], "更新后的决策问题，仅用于任务边界。")
        self.assertEqual(projection["questions"][1]["status"], "waiting_for_material")
        self.assertEqual(projection["questions"][1]["status_label"], "待补料")
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        review = build_review_context(self.repository, "DB-001")
        self.assertEqual(review["block"]["current_text"], before_text)

    def test_unknown_question_and_empty_fields_are_rejected(self) -> None:
        with self.assertRaises(BriefUpdateError):
            update_brief(
                self.repository,
                self.project_id,
                questions=[{"id": "RQ-99", "question": "不存在的问题"}],
            )
        with self.assertRaises(BriefUpdateError):
            update_brief(self.repository, self.project_id, deliverable="   ")
        with self.assertRaises(BriefUpdateError):
            update_brief(self.repository, "missing")


class BriefViewHttpTest(unittest.TestCase):
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

    def test_http_matches_projection(self) -> None:
        status, payload = _http_json("GET", self.server.origin + "/projects/P-DEMO-001/brief")
        dispatched = dispatch_get(self.repository, "/projects/P-DEMO-001/brief")
        projection = build_brief_projection(self.repository, "P-DEMO-001")
        self.assertEqual(status, 200)
        self.assertEqual(dispatched, (200, projection))
        self.assertEqual(payload, projection)
        self.assertEqual(payload["brief"]["id"], "B-DEMO-001")

    def test_http_save_matches_application(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects/P-DEMO-001/brief",
            {"deliverable": "更新后的内部研究恢复稿"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["brief_projection"]["brief"]["deliverable"], "更新后的内部研究恢复稿")
        dispatched = dispatch_post(
            self.repository,
            "/projects/P-DEMO-001/brief",
            {"decision_question": "第二次更新决策问题。"},
        )
        self.assertEqual(dispatched[0], 200)
        self.assertEqual(
            dispatched[1]["brief_projection"]["brief"]["decision_question"],
            "第二次更新决策问题。",
        )

    def test_missing_project_is_404(self) -> None:
        status, payload = _http_json("GET", self.server.origin + "/projects/missing/brief")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)


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
