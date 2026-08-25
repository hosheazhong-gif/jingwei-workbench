from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_delete, dispatch_post
from app.application.add_block import (
    BlockWriteError,
    add_deliverable_block,
    remove_deliverable_block,
    rename_deliverable_block,
)
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class AddDeliverableBlockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()

    def test_adds_manual_block_without_claims(self) -> None:
        created = create_project(
            self.repository,
            name="第二个真实题目",
            original_context="评估某园区冷库改造是否值得继续谈。",
        )
        project_id = created["project_id"]
        placeholder = created["report"]["blocks"][0]["current_text"]
        result = add_deliverable_block(
            self.repository,
            project_id,
            title="本轮已知缺口",
            current_text="租户结构仍不清楚，不能据此判断改造必要性。",
        )
        report = result["report"]
        self.assertEqual(result["block_id"], "DB-002")
        self.assertEqual(len(report["blocks"]), 2)
        self.assertEqual(report["blocks"][0]["current_text"], placeholder)
        self.assertEqual(report["blocks"][1]["title"], "本轮已知缺口")
        self.assertEqual(
            report["blocks"][1]["current_text"],
            "租户结构仍不清楚，不能据此判断改造必要性。",
        )
        self.assertEqual(report["blocks"][1]["claim_ids"], [])
        review = result["review_context"]
        self.assertEqual(review["block"]["id"], "DB-002")
        self.assertEqual(review["claims"], [])
        self.assertIn("尚无来源与主张", report["blocks"][1]["restriction"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertTrue(result["confirmation"]["existing_current_text_unchanged"])
        self.assertEqual(result["confirmation"]["record_kind"], "add_block")
        self.assertEqual(result["confirmation"]["treatment"], "新增段落，未生成主张")
        with self.repository.connect() as connection:
            claim_count = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            adopted = connection.execute(
                """
                SELECT version, adopted FROM deliverable_block_revisions
                WHERE deliverable_block_id = ?
                """,
                ("DB-002",),
            ).fetchone()
        self.assertEqual(claim_count, 0)
        self.assertEqual(adopted["version"], 1)
        self.assertEqual(adopted["adopted"], 1)

    def test_does_not_rewrite_synthetic_sample(self) -> None:
        synthetic_id = import_sample(self.repository, SAMPLE_PATH)
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        created = create_project(
            self.repository,
            name="另一题",
            original_context="另一句任务。",
        )
        add_deliverable_block(
            self.repository,
            created["project_id"],
            title="人工段落",
            current_text="只写这一题的缺口。",
        )
        synthetic = build_report_projection(self.repository, synthetic_id)
        self.assertEqual(len(synthetic["blocks"]), 4)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(build_review_context(self.repository, "DB-001")["claims"][0]["id"], "C-001")

    def test_renames_added_block_without_rewriting_draft(self) -> None:
        created = create_project(
            self.repository,
            name="可改名",
            original_context="加一节后改名。",
        )
        added = add_deliverable_block(
            self.repository,
            created["project_id"],
            title="未命名一节",
            current_text="这一节还没写。",
        )
        before_text = added["report"]["blocks"][1]["current_text"]
        result = rename_deliverable_block(
            self.repository, added["block_id"], title="租户缺口"
        )
        block = next(
            item for item in result["report"]["blocks"] if item["id"] == added["block_id"]
        )
        self.assertEqual(block["title"], "租户缺口")
        self.assertEqual(block["current_text"], before_text)
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertEqual(result["confirmation"]["record_kind"], "rename_block")
        self.assertEqual(
            result["workbench"]["blocks"][-1]["title"],
            "租户缺口",
        )
        with self.assertRaisesRegex(BlockWriteError, "节名不能为空"):
            rename_deliverable_block(self.repository, added["block_id"], title="  ")

    def test_rename_does_not_rewrite_synthetic_sample(self) -> None:
        import_sample(self.repository, SAMPLE_PATH)
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        before_version = _block_version(self.repository, "DB-001")
        result = rename_deliverable_block(
            self.repository, "DB-001", title="项目问题（本轮）"
        )
        self.assertEqual(result["report"]["blocks"][0]["title"], "项目问题（本轮）")
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_version(self.repository, "DB-001"), before_version)

    def test_removes_empty_extra_block_without_rewriting_synthetic(self) -> None:
        synthetic_id = import_sample(self.repository, SAMPLE_PATH)
        created = create_project(
            self.repository,
            name="可去掉空节",
            original_context="先加一节再去掉。",
        )
        added = add_deliverable_block(
            self.repository,
            created["project_id"],
            title="未命名一节",
            current_text="这一节还没写。",
        )
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = remove_deliverable_block(self.repository, added["block_id"])
        self.assertTrue(result["deleted"])
        self.assertEqual(len(result["report"]["blocks"]), 1)
        with self.assertRaisesRegex(BlockWriteError, "至少要留一节"):
            remove_deliverable_block(self.repository, result["report"]["blocks"][0]["id"])
        with self.assertRaisesRegex(BlockWriteError, "已挂主张"):
            remove_deliverable_block(self.repository, "DB-001")
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(len(build_report_projection(self.repository, synthetic_id)["blocks"]), 4)

    def test_rejects_empty_title_or_body(self) -> None:
        project_id = create_project(
            self.repository,
            name="缺字段题目",
            original_context="有情境。",
        )["project_id"]
        with self.assertRaisesRegex(BlockWriteError, "段落标题"):
            add_deliverable_block(
                self.repository,
                project_id,
                title="  ",
                current_text="有正文",
            )
        with self.assertRaisesRegex(BlockWriteError, "段落正文"):
            add_deliverable_block(
                self.repository,
                project_id,
                title="有标题",
                current_text="",
            )
        self.assertEqual(
            len(build_report_projection(self.repository, project_id)["blocks"]),
            1,
        )

    def test_missing_project_is_rejected(self) -> None:
        with self.assertRaisesRegex(BlockWriteError, "不存在"):
            add_deliverable_block(
                self.repository,
                "P-MISSING",
                title="标题",
                current_text="正文",
            )


class AddDeliverableBlockHttpTest(unittest.TestCase):
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

    def test_http_adds_block_and_keeps_synthetic(self) -> None:
        created_status, created = _http_json(
            "POST",
            self.server.origin + "/projects",
            {
                "name": "HTTP 人工段落题目",
                "original_context": "建题后人工写一段。",
            },
        )
        self.assertEqual(created_status, 201)
        project_id = created["project_id"]
        status, payload = _http_json(
            "POST",
            self.server.origin + f"/projects/{project_id}/deliverable-blocks",
            {
                "title": "缺口判断",
                "current_text": "目前没有独立核实的租户结构。",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(len(payload["report"]["blocks"]), 2)
        self.assertEqual(payload["report"]["blocks"][1]["title"], "缺口判断")
        self.assertEqual(payload["review_context"]["claims"], [])
        synthetic = build_report_projection(self.repository, "P-DEMO-001")
        self.assertEqual(len(synthetic["blocks"]), 4)
        dispatched = dispatch_post(
            self.repository,
            f"/projects/{project_id}/deliverable-blocks",
            {"title": "", "current_text": "正文"},
        )
        self.assertEqual(dispatched[0], 400)
        missing = dispatch_post(
            self.repository,
            "/projects/P-MISSING/deliverable-blocks",
            {"title": "标题", "current_text": "正文"},
        )
        self.assertEqual(missing[0], 404)

    def test_http_removes_empty_block(self) -> None:
        created_status, created = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "HTTP 去掉空节", "original_context": "加完再去掉。"},
        )
        self.assertEqual(created_status, 201)
        added_status, added = _http_json(
            "POST",
            self.server.origin + f"/projects/{created['project_id']}/deliverable-blocks",
            {"title": "未命名一节", "current_text": "这一节还没写。"},
        )
        self.assertEqual(added_status, 201)
        status, payload = _http_json(
            "DELETE",
            self.server.origin + f"/deliverable-blocks/{added['block_id']}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["report"]["blocks"]), 1)
        rejected = dispatch_delete(self.repository, "/deliverable-blocks/DB-001")
        self.assertEqual(rejected[0], 400)

    def test_http_renames_added_block(self) -> None:
        created_status, created = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "HTTP 改节名", "original_context": "加完再改名。"},
        )
        self.assertEqual(created_status, 201)
        added_status, added = _http_json(
            "POST",
            self.server.origin + f"/projects/{created['project_id']}/deliverable-blocks",
            {"title": "未命名一节", "current_text": "这一节还没写。"},
        )
        self.assertEqual(added_status, 201)
        status, payload = _http_json(
            "POST",
            self.server.origin + f"/deliverable-blocks/{added['block_id']}/title",
            {"title": "租户缺口"},
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["confirmation"]["record_kind"], "rename_block")
        self.assertTrue(payload["confirmation"]["current_text_unchanged"])
        self.assertEqual(
            payload["workbench"]["blocks"][-1]["title"],
            "租户缺口",
        )
        empty = dispatch_post(
            self.repository,
            f"/deliverable-blocks/{added['block_id']}/title",
            {"title": ""},
        )
        self.assertEqual(empty[0], 400)
        missing = dispatch_post(
            self.repository,
            "/deliverable-blocks/DB-MISSING/title",
            {"title": "有名字"},
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


def _block_version(repository: SqliteRepository, block_id: str) -> int:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT current_version FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()["current_version"]


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
