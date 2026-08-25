from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.api.server import dispatch_delete
from app.application.create_project import create_project
from app.application.delete_project import ProjectDeleteError, delete_project
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class DeleteProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.synthetic_id = import_sample(self.repository, SAMPLE_PATH)

    def test_delete_created_project_does_not_touch_synthetic(self) -> None:
        created = create_project(
            self.repository,
            name="可删除题目",
            original_context="建完再删，不得改写远川园区样本。",
        )
        project_id = created["project_id"]
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = delete_project(self.repository, project_id)
        self.assertTrue(result["deleted"])
        self.assertFalse(self.repository.has_project(project_id))
        self.assertTrue(self.repository.has_project(self.synthetic_id))
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        synthetic = build_report_projection(self.repository, self.synthetic_id)
        self.assertEqual(len(synthetic["blocks"]), 4)
        names = [item["name"] for item in result["projects"]["projects"]]
        self.assertNotIn("可删除题目", names)
        self.assertIn("远川食品产业园匿名研究", names)

    def test_missing_project_is_rejected(self) -> None:
        with self.assertRaises(ProjectDeleteError):
            delete_project(self.repository, "P-MISSING")


class DeleteProjectHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        import_sample(self.repository, SAMPLE_PATH)
        self.created_id = create_project(
            self.repository,
            name="HTTP 删除题目",
            original_context="用于接口删除。",
        )["project_id"]
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_http_delete_matches_application(self) -> None:
        status, payload = _http_json(
            "DELETE", self.server.origin + "/projects/" + self.created_id
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["deleted"])
        self.assertFalse(self.repository.has_project(self.created_id))
        dispatched = dispatch_delete(self.repository, "/projects/P-MISSING")
        self.assertEqual(dispatched[0], 404)
        synthetic = build_report_projection(self.repository, "P-DEMO-001")
        self.assertEqual(len(synthetic["blocks"]), 4)


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
