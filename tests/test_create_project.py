from __future__ import annotations

import json
import tempfile
import unittest
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_get, dispatch_post
from app.application.create_project import (
    PLACEHOLDER_TEXT,
    ProjectCreateError,
    create_project,
    ensure_review_shell,
)
from app.cli import main as cli_main
from app.application.import_sample import import_sample
from app.application.update_brief import update_brief
from app.projections.brief import build_brief_projection
from app.projections.projects import build_project_list_projection
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class CreateProjectTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()

    def test_blank_project_writes_brief_not_draft(self) -> None:
        result = create_project(
            self.repository,
            name="第二个真实题目",
            original_context="一句话：评估某园区冷库改造是否值得继续谈。",
            questions=["租户结构是否清楚", "改造门槛是什么"],
        )
        project_id = result["project_id"]
        report = build_report_projection(self.repository, project_id)
        brief = build_brief_projection(self.repository, project_id)
        self.assertEqual(project_id, "P-001")
        self.assertEqual(result["brief_id"], "B-001")
        self.assertEqual(report["project"]["name"], "第二个真实题目")
        self.assertEqual(len(report["blocks"]), 1)
        self.assertEqual(report["blocks"][0]["title"], "未命名的一节")
        self.assertEqual(report["blocks"][0]["current_text"], PLACEHOLDER_TEXT)
        self.assertEqual(report["blocks"][0]["claim_ids"], [])
        review = build_review_context(self.repository, report["blocks"][0]["id"])
        self.assertEqual(review["claims"], [])
        self.assertEqual(brief["brief"]["original_context"], "一句话：评估某园区冷库改造是否值得继续谈。")
        self.assertEqual(brief["brief"]["decision_question"], brief["brief"]["original_context"])
        self.assertEqual(brief["brief"]["deliverable"], "内部研究初稿")
        self.assertEqual(
            [item["question"] for item in brief["questions"]],
            ["租户结构是否清楚", "改造门槛是什么"],
        )
        self.assertTrue(result["confirmation"]["did_not_overwrite_existing"])
        with self.repository.connect() as connection:
            claim_count = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
            source_count = connection.execute(
                "SELECT COUNT(*) FROM sources WHERE project_id = ?",
                (project_id,),
            ).fetchone()[0]
        self.assertEqual(claim_count, 0)
        self.assertEqual(source_count, 0)

    def test_create_does_not_overwrite_synthetic_sample(self) -> None:
        synthetic_id = import_sample(self.repository, SAMPLE_PATH)
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        created = create_project(
            self.repository,
            name="另一题",
            original_context="新的一句话任务，不得抄写样本正文。",
        )
        synthetic_report = build_report_projection(self.repository, synthetic_id)
        listing = build_project_list_projection(self.repository)
        names = [item["name"] for item in listing["projects"]]
        self.assertNotEqual(created["project_id"], synthetic_id)
        self.assertEqual({block["id"] for block in synthetic_report["blocks"]}, {"DB-001", "DB-002", "DB-003", "DB-004"})
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertIn("远川食品产业园匿名研究", names)
        self.assertIn("另一题", names)
        self.assertNotIn("60%+", created["brief_projection"]["brief"]["original_context"])
        self.assertNotEqual(created["report"]["blocks"][0]["id"], "DB-001")
        self.assertNotIn("60%+", created["report"]["blocks"][0]["current_text"])
        self.assertEqual(created["brief_projection"]["questions"], [])

    def test_demo_template_keeps_labels_without_auto_seed(self) -> None:
        created = create_project(
            self.repository,
            name="接缝演练题目",
            original_context="观察某个市场对象本轮要不要跟进。",
            template_key="demo_market_scan",
        )
        self.assertEqual(created["brief_projection"]["questions"], [])
        self.assertEqual(len(created["report"]["blocks"]), 1)
        self.assertEqual(created["report"]["blocks"][0]["current_text"], PLACEHOLDER_TEXT)
        from app.templates.registry import load_template

        self.assertEqual(
            list(load_template("demo_market_scan").recommended_question_labels()),
            ["市场现状", "主要变化", "待核实项"],
        )
        empty_questions = create_project(
            self.repository,
            name="显式空问题",
            original_context="不使用模板推荐问题。",
            template_key="demo_market_scan",
            questions=[],
        )
        self.assertEqual(empty_questions["brief_projection"]["questions"], [])

    def test_missing_fields_and_unknown_template_are_rejected(self) -> None:
        with self.assertRaises(ProjectCreateError):
            create_project(self.repository, name="  ", original_context="有情境")
        with self.assertRaises(ProjectCreateError):
            create_project(self.repository, name="有名称", original_context="   ")
        with self.assertRaises(ProjectCreateError):
            create_project(
                self.repository,
                name="有名称",
                original_context="有情境",
                template_key="missing_template",
            )


class CreateProjectCliTest(unittest.TestCase):
    def test_create_project_command_is_a_working_developer_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = Path(temp_dir) / "jingwei.sqlite3"
            argv = [
                "app.cli",
                "--db",
                str(database),
                "create-project",
                "--name",
                "CLI 新题目",
                "--original-context",
                "经理只给了一句话。",
                "--template",
                "competitive_intel_sweep",
                "--question",
                "首轮必须回答什么？",
            ]
            output = StringIO()
            with patch.object(sys, "argv", argv), patch("sys.stdout", output):
                cli_main()

            created = json.loads(output.getvalue())
            self.assertEqual(
                created["report"]["project"]["template_key"],
                "competitive_intel_sweep",
            )
            repository = SqliteRepository(database)
            listing = build_project_list_projection(repository)
            self.assertIn("CLI 新题目", [item["name"] for item in listing["projects"]])


class CreateProjectHttpTest(unittest.TestCase):
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

    def test_http_list_and_create_share_projection(self) -> None:
        list_status, listing = _http_json("GET", self.server.origin + "/projects")
        self.assertEqual(list_status, 200)
        self.assertEqual(dispatch_get(self.repository, "/projects"), (200, listing))
        self.assertEqual(listing["projects"][0]["id"], "P-DEMO-001")
        self.assertEqual(listing["projects"][0]["name"], "远川食品产业园匿名研究")
        self.assertTrue(listing["projects"][0]["decision"])
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects",
            {
                "name": "HTTP 新建题目",
                "original_context": "用一句话建题，不要生成内部稿。",
                "questions": ["缺口是什么"],
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(payload["project_id"], "P-001")
        self.assertEqual(len(payload["report"]["blocks"]), 1)
        self.assertEqual(payload["report"]["blocks"][0]["claim_ids"], [])
        dispatched = dispatch_post(
            self.repository,
            "/projects",
            {"name": "第二次新建", "original_context": "第二句任务。"},
        )
        self.assertEqual(dispatched[0], 201)
        self.assertEqual(dispatched[1]["project_id"], "P-002")
        synthetic = build_report_projection(self.repository, "P-DEMO-001")
        self.assertEqual(len(synthetic["blocks"]), 4)
        shell_status, shell = _http_json(
            "POST", self.server.origin + "/projects/P-DEMO-001/review-shell", {}
        )
        self.assertEqual(shell_status, 200)
        self.assertFalse(shell["created"])
        self.assertEqual(shell["block_id"], "DB-001")
        dispatched = dispatch_post(
            self.repository, "/projects/P-DEMO-001/review-shell", {}
        )
        self.assertEqual(dispatched[0], 200)
        self.assertFalse(dispatched[1]["created"])

    def test_http_rejects_empty_name(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "", "original_context": "有情境"},
        )
        self.assertEqual(status, 400)
        self.assertIn("题目名称", payload["error"])


class CreateProjectBriefExtendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = create_project(
            self.repository,
            name="可补问题的题目",
            original_context="先建题，再补本轮问题。",
            questions=[],
        )["project_id"]

    def test_blank_question_id_inserts_without_draft(self) -> None:
        result = update_brief(
            self.repository,
            self.project_id,
            questions=[{"question": "后来补上的问题", "status": "not_started"}],
        )
        questions = result["brief_projection"]["questions"]
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["question"], "后来补上的问题")
        self.assertTrue(questions[0]["id"].startswith("RQ-"))
        blocks = build_report_projection(self.repository, self.project_id)["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["current_text"], PLACEHOLDER_TEXT)


class EnsureReviewShellTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.synthetic_id = import_sample(self.repository, SAMPLE_PATH)

    def test_ensure_does_not_rewrite_existing_draft(self) -> None:
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = ensure_review_shell(self.repository, self.synthetic_id)
        self.assertFalse(result["created"])
        self.assertEqual(result["block_id"], "DB-001")
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)

    def test_ensure_adds_shell_only_when_empty(self) -> None:
        with self.repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO projects (
                    id, name, template_key, execution_strategy_key, stage, decision_gate,
                    schema_version, created_at, updated_at
                ) VALUES (
                    'P-EMPTY', '空题目', 'industry_chain_analysis_presales', 'fixed_workflow',
                    'intake', 'brainstorm_ready', '0.3', '2026-08-14', '2026-08-14'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO briefs (
                    id, project_id, original_context, decision_question, deliverable,
                    not_a_final_client_recommendation, schema_version, created_at, updated_at
                ) VALUES (
                    'B-EMPTY', 'P-EMPTY', '空题目情境', '空题目决策', '内部研究初稿',
                    1, '0.3', '2026-08-14', '2026-08-14'
                )
                """
            )
        before_status = _claim_status(self.repository, "C-002")
        result = ensure_review_shell(self.repository, "P-EMPTY")
        self.assertTrue(result["created"])
        self.assertEqual(len(result["report"]["blocks"]), 1)
        self.assertEqual(result["report"]["blocks"][0]["claim_ids"], [])
        self.assertEqual(result["report"]["blocks"][0]["current_text"], PLACEHOLDER_TEXT)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        again = ensure_review_shell(self.repository, "P-EMPTY")
        self.assertFalse(again["created"])
        self.assertEqual(again["block_id"], result["block_id"])


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
