from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_get
from app.application.attach_claim import attach_claim_to_block, unlink_claim_from_block
from app.application.candidate_source import (
    capture_web_candidate,
    discard_web_candidate,
)
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.application.question_progress import (
    QuestionProgressError,
    add_research_question,
    defer_research_question,
    restore_research_question,
    set_question_progress,
)
from app.application.review_block import adopt_revision, propose_block_revision
from app.application.update_brief import update_brief
from app.projections.brief import build_brief_projection
from app.projections.report import build_report_projection
from app.projections.sources import build_source_list_projection
from app.projections.workbench import (
    _unsourced_numbers,
    build_workbench_projection,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class WorkbenchProjectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_workbench_reads_same_ids_as_brief_report_and_sources(self) -> None:
        workbench = build_workbench_projection(self.repository, self.project_id)
        brief = build_brief_projection(self.repository, self.project_id)
        report = build_report_projection(self.repository, self.project_id)
        sources = build_source_list_projection(self.repository, self.project_id)
        self.assertEqual(workbench["project"]["id"], self.project_id)
        self.assertEqual(workbench["decision"], brief["brief"]["decision_question"])
        self.assertEqual(
            [item["id"] for item in workbench["questions"]],
            [item["id"] for item in brief["questions"]],
        )
        self.assertEqual(workbench["deferred_questions"], [])
        self.assertTrue(workbench["questions"][0]["can_defer"])
        self.assertFalse(workbench["questions"][0]["deferred"])
        self.assertEqual(
            [item["id"] for item in workbench["blocks"]],
            [item["id"] for item in report["blocks"]],
        )
        self.assertEqual(
            [item["id"] for item in workbench["materials"]["sources"]],
            [item["id"] for item in sources["sources"]],
        )
        self.assertEqual(workbench["questions"][0]["progress"], "unwritten")
        self.assertEqual(workbench["questions"][0]["progress_label"], "还没写")
        self.assertFalse(workbench["blocks"][0]["can_remove"])
        self.assertEqual(
            workbench["blocks"][0]["current_text"],
            report["blocks"][0]["current_text"],
        )
        body = next(
            item for item in workbench["materials"]["sources"] if item["id"] == "S-002"
        )
        self.assertIn("60%+ 食品产业客群", [item["text"] for item in body["excerpts"]])
        self.assertTrue(body["limitation"])
        self.assertNotIn("id", body["excerpts"][0])
        self.assertNotIn("C-002", json.dumps(body, ensure_ascii=False))

    def test_set_aside_dedupes_same_url_discarded_more_than_once(self) -> None:
        # 现场缺陷（docs/20 §6，2026-08-22 未修条）：同一条链接被排除两次会在
        # 「这轮不用的」抽屉里留下两条标题相同、状态各自独立的候选，像是有一条
        # 被谁换掉了。这里直接构造历史遗留的重复数据（不经过已经堵住根因的
        # 搜索路径），确认展示层会把它们并成一条；对象本身仍都保留，不删除。
        first = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/set-aside-dup",
            title="重复候选",
        )
        discard_web_candidate(self.repository, first["candidate_id"])
        second = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/set-aside-dup",
            title="重复候选",
        )
        discard_web_candidate(self.repository, second["candidate_id"])
        workbench = build_workbench_projection(self.repository, self.project_id)
        matching = [
            item
            for item in workbench["materials"]["set_aside"]
            if item["url"] == "https://example.com/set-aside-dup"
        ]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["id"], second["candidate_id"])
        with self.repository.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM candidate_sources WHERE url = ?",
                ("https://example.com/set-aside-dup",),
            ).fetchall()
        self.assertEqual({row["id"] for row in rows}, {first["candidate_id"], second["candidate_id"]})

    def test_blank_project_materials_do_not_list_other_project_sources(self) -> None:
        created = create_project(
            self.repository,
            name="空白隔离题",
            original_context="只用来确认材料匣不串题。",
        )
        incoming = Path(self.temp_dir.name) / "broker.txt"
        incoming.write_text("名单仅属于空白题。\n", encoding="utf-8")
        capture_local_source(
            self.repository,
            created["project_id"],
            incoming,
            title="头部券商公司名单",
        )
        synthetic = build_workbench_projection(self.repository, self.project_id)
        blank = build_workbench_projection(self.repository, created["project_id"])
        synthetic_titles = [item["title"] for item in synthetic["materials"]["sources"]]
        blank_titles = [item["title"] for item in blank["materials"]["sources"]]
        self.assertIn("项目本体分析页", synthetic_titles)
        self.assertNotIn("头部券商公司名单", synthetic_titles)
        self.assertEqual(blank_titles, ["头部券商公司名单"])
        self.assertNotIn("项目本体分析页", blank_titles)

    def test_hanging_excerpt_shows_on_materials_without_rewriting_draft(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        before_status = _claim_status(self.repository, "C-002")
        incoming = Path(self.temp_dir.name) / "note.txt"
        incoming.write_text("现场租户仍待核。\n", encoding="utf-8")
        source_id = capture_local_source(
            self.repository,
            self.project_id,
            incoming,
            title="现场笔记",
        )["source"]["id"]
        attach_claim_to_block(
            self.repository,
            "DB-001",
            source_id=source_id,
            excerpt="现场租户仍待核。",
        )
        workbench = build_workbench_projection(self.repository, self.project_id)
        material = next(
            item for item in workbench["materials"]["sources"] if item["id"] == source_id
        )
        self.assertIn("现场租户仍待核。", [item["text"] for item in material["excerpts"]])
        block = next(item for item in workbench["blocks"] if item["id"] == "DB-001")
        self.assertIn("现场租户仍待核。", [item["excerpt"] for item in block["claim_sources"]])
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)

    def test_unlink_hides_excerpt_from_section_without_deleting_claim(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        before_status = _claim_status(self.repository, "C-002")
        result = unlink_claim_from_block(self.repository, "DB-001", "C-002")
        block = next(
            item for item in result["workbench"]["blocks"] if item["id"] == "DB-001"
        )
        self.assertNotIn("C-002", [item["claim_id"] for item in block["claim_sources"]])
        material = next(
            item
            for item in result["workbench"]["materials"]["sources"]
            if item["id"] == "S-002"
        )
        self.assertIn("60%+ 食品产业客群", [item["text"] for item in material["excerpts"]])
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)

    def test_client_number_is_not_marked_unsourced(self) -> None:
        workbench = build_workbench_projection(self.repository, self.project_id)
        block = next(item for item in workbench["blocks"] if item["id"] == "DB-001")
        self.assertEqual(block["checks"]["unsourced_numbers"], [])
        self.assertTrue(
            any(item.get("client_provided") for item in block["claim_sources"])
        )

    def test_unsourced_number_helper_flags_invented_stats(self) -> None:
        found = _unsourced_numbers("产值约 6000 亿。", ["客群以食品为主"])
        self.assertEqual([item["text"] for item in found], ["6000"])
        self.assertEqual(_unsourced_numbers("1. 现有租户\n2. 空间条件", []), [])

    def test_preview_flags_unsourced_number_before_adopt(self) -> None:
        propose_block_revision(
            self.repository,
            "DB-004",
            body="这一节改成缺口稿：产值约 6000亿，口径待补。",
        )
        workbench = build_workbench_projection(self.repository, self.project_id)
        block = next(item for item in workbench["blocks"] if item["id"] == "DB-004")
        self.assertEqual(
            [item["text"] for item in block["preview_checks"]["unsourced_numbers"]],
            ["6000亿"],
        )
        self.assertTrue(block["preview_checks"]["novel_claims"])
        self.assertEqual(block["checks"]["unsourced_numbers"], [])
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")
        self.assertNotEqual(
            block["current_text"],
            "这一节改成缺口稿：产值约 6000亿，口径待补。",
        )

    def test_replacing_source_marks_affected_block_stale(self) -> None:
        incoming = Path(self.temp_dir.name) / "newer.txt"
        incoming.write_text("replacement-copy\n", encoding="utf-8")
        before = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        capture_local_source(
            self.repository,
            self.project_id,
            incoming,
            title="更新后的客户材料",
            supersedes_source_id="S-002",
        )
        workbench = build_workbench_projection(self.repository, self.project_id)
        block = next(item for item in workbench["blocks"] if item["id"] == "DB-001")
        materials = workbench["materials"]["sources"]
        old = next(item for item in materials if item["id"] == "S-002")
        new = next(item for item in materials if item["title"] == "更新后的客户材料")
        self.assertTrue(block["checks"]["stale"])
        self.assertTrue(old["superseded"])
        self.assertFalse(new["superseded"])
        self.assertEqual(new["supersedes_source_id"], "S-002")
        self.assertEqual(new["supersedes_title"], old["title"])
        self.assertEqual(_claim_status(self.repository, "C-002"), before)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)

    def test_updating_decision_does_not_rewrite_draft(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        update_brief(
            self.repository,
            self.project_id,
            decision_question="这轮只判断要不要继续跟。",
        )
        workbench = build_workbench_projection(self.repository, self.project_id)
        self.assertEqual(workbench["decision"], "这轮只判断要不要继续跟。")
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def test_progress_does_not_change_verification_or_draft(self) -> None:
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        result = set_question_progress(self.repository, "RQ-01", "enough")
        question = next(
            item for item in result["workbench"]["questions"] if item["id"] == "RQ-01"
        )
        self.assertEqual(question["progress"], "enough")
        self.assertEqual(question["progress_label"], "这轮够用了")
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        with self.assertRaises(QuestionProgressError):
            set_question_progress(self.repository, "RQ-01", "verified")

    def test_defer_and_restore_do_not_delete_or_rewrite_draft(self) -> None:
        before_status = _claim_status(self.repository, "C-002")
        before_text = _block_text(self.repository, "DB-001")
        deferred = defer_research_question(self.repository, "RQ-06")
        active_ids = [item["id"] for item in deferred["workbench"]["questions"]]
        deferred_ids = [
            item["id"] for item in deferred["workbench"]["deferred_questions"]
        ]
        self.assertNotIn("RQ-06", active_ids)
        self.assertEqual(deferred_ids, ["RQ-06"])
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        brief = build_brief_projection(self.repository, self.project_id)
        self.assertIn("RQ-06", [item["id"] for item in brief["questions"]])
        with self.assertRaises(QuestionProgressError):
            set_question_progress(self.repository, "RQ-06", "draft")
        restored = restore_research_question(self.repository, "RQ-06")
        restored_ids = [item["id"] for item in restored["workbench"]["questions"]]
        self.assertIn("RQ-06", restored_ids)
        self.assertEqual(restored["workbench"]["deferred_questions"], [])
        question = next(
            item
            for item in restored["workbench"]["questions"]
            if item["id"] == "RQ-06"
        )
        self.assertEqual(question["progress"], "unwritten")
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)

    def test_cannot_defer_last_active_question(self) -> None:
        defer_research_question(self.repository, "RQ-01")
        defer_research_question(self.repository, "RQ-02")
        defer_research_question(self.repository, "RQ-04")
        workbench = build_workbench_projection(self.repository, self.project_id)
        self.assertEqual([item["id"] for item in workbench["questions"]], ["RQ-06"])
        self.assertFalse(workbench["questions"][0]["can_defer"])
        with self.assertRaises(QuestionProgressError):
            defer_research_question(self.repository, "RQ-06")
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def test_add_question_writes_same_table_not_draft(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        result = add_research_question(
            self.repository,
            self.project_id,
            question="改造后谁来运营？",
        )
        self.assertTrue(result["question_id"].startswith("RQ-"))
        added = next(
            item
            for item in result["workbench"]["questions"]
            if item["id"] == result["question_id"]
        )
        self.assertEqual(added["question"], "改造后谁来运营？")
        self.assertEqual(added["progress"], "unwritten")
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        with self.assertRaises(QuestionProgressError):
            add_research_question(self.repository, self.project_id, question="  ")


class WorkbenchHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_get_workbench_matches_projection(self) -> None:
        status, payload = self._get(f"/projects/{self.project_id}/workbench")
        self.assertEqual(status, 200)
        self.assertEqual(
            payload, build_workbench_projection(self.repository, self.project_id)
        )
        dispatch_status, dispatch_payload = dispatch_get(
            self.repository, f"/projects/{self.project_id}/workbench"
        )
        self.assertEqual((status, payload), (dispatch_status, dispatch_payload))

    def test_post_progress_returns_workbench(self) -> None:
        status, payload = self._post(
            "/research-questions/RQ-02/progress", {"progress": "draft"}
        )
        self.assertEqual(status, 200)
        question = next(
            item for item in payload["workbench"]["questions"] if item["id"] == "RQ-02"
        )
        self.assertEqual(question["progress"], "draft")
        self.assertTrue(payload["confirmation"]["verification_status_unchanged"])
        self.assertTrue(payload["confirmation"]["current_text_unchanged"])

    def test_post_add_defer_restore_returns_workbench(self) -> None:
        status, payload = self._post(
            f"/projects/{self.project_id}/research-questions",
            {"question": "谁来承担改造后的运营？"},
        )
        self.assertEqual(status, 201)
        added_id = payload["question_id"]
        self.assertTrue(
            any(item["id"] == added_id for item in payload["workbench"]["questions"])
        )
        deferred = self._post(f"/research-questions/{added_id}/defer", {})
        self.assertEqual(deferred[0], 200)
        self.assertEqual(
            [item["id"] for item in deferred[1]["workbench"]["deferred_questions"]],
            [added_id],
        )
        restored = self._post(f"/research-questions/{added_id}/restore", {})
        self.assertEqual(restored[0], 200)
        self.assertTrue(
            any(
                item["id"] == added_id
                for item in restored[1]["workbench"]["questions"]
            )
        )
        self.assertTrue(restored[1]["confirmation"]["verification_status_unchanged"])
        self.assertTrue(restored[1]["confirmation"]["current_text_unchanged"])

    def test_post_brief_updates_decision_not_draft(self) -> None:
        before = _block_text(self.repository, "DB-001")
        status, payload = self._post(
            f"/projects/{self.project_id}/brief",
            {"decision_question": "这轮只判断要不要继续跟。"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["confirmation"]["current_text_unchanged"])
        workbench = build_workbench_projection(self.repository, self.project_id)
        self.assertEqual(workbench["decision"], "这轮只判断要不要继续跟。")
        self.assertEqual(_block_text(self.repository, "DB-001"), before)

    def test_workbench_post_is_rejected_as_readonly(self) -> None:
        status, payload = self._post(
            f"/projects/{self.project_id}/workbench", {}
        )
        self.assertEqual(status, 405)
        self.assertIn("只读", payload["error"])

    def test_blank_project_workbench_allows_gap_placeholder(self) -> None:
        created = create_project(
            self.repository,
            name="空白工作台",
            original_context="经理要判断这个点还值不值得跟。",
        )
        workbench = build_workbench_projection(
            self.repository, created["project_id"]
        )
        self.assertEqual(workbench["decision"], "经理要判断这个点还值不值得跟。")
        self.assertTrue(workbench["blocks"][0]["placeholder"])

    def test_pending_revision_appears_on_workbench_block(self) -> None:
        propose_block_revision(
            self.repository,
            "DB-004",
            body="这一节改成缺口稿：还缺项目本体材料。",
        )
        workbench = build_workbench_projection(self.repository, self.project_id)
        block = next(item for item in workbench["blocks"] if item["id"] == "DB-004")
        self.assertIsNotNone(block["pending_revision"])
        self.assertIn("缺口稿", block["pending_revision"]["body"])

    def test_adopted_block_exposes_prior_revision_for_restore(self) -> None:
        before = _block_text(self.repository, "DB-004")
        proposed = propose_block_revision(
            self.repository,
            "DB-004",
            body="这一节改成缺口稿：还缺项目本体材料。",
        )
        adopt_revision(
            self.repository, "DB-004", proposed["pending_revision"]["version"]
        )
        workbench = build_workbench_projection(self.repository, self.project_id)
        block = next(item for item in workbench["blocks"] if item["id"] == "DB-004")
        self.assertEqual(block["current_text"], "这一节改成缺口稿：还缺项目本体材料。")
        self.assertIsNotNone(block["prior_revision"])
        self.assertEqual(block["prior_revision"]["body"], before)
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")

    def _get(self, path: str) -> tuple[int, dict]:
        request = Request(self.server.origin + path, method="GET")
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            payload = json.loads(error.read().decode("utf-8"))
            error.close()
            return error.code, payload

    def _post(self, path: str, payload: dict) -> tuple[int, dict]:
        request = Request(
            self.server.origin + path,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            body = json.loads(error.read().decode("utf-8"))
            error.close()
            return error.code, body


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


if __name__ == "__main__":
    unittest.main()
