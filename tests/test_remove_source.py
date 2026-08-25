from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_delete
from app.application.candidate_source import (
    capture_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.create_project import create_project
from app.application.excerpt_from_snapshot import (
    adopt_snapshot_excerpts,
    draft_snapshot_excerpts,
)
from app.application.import_sample import import_sample
from app.application.remove_source import SourceRemoveError, remove_source
from app.application.source_snapshot import resolve_source_snapshot_path
from tests.test_excerpt_from_snapshot import RichWebAdapter, ScriptedExcerptAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class RemoveSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(
            self.database_path, files_root=Path(self.temp_dir.name) / "files"
        )
        self.repository.migrate()
        # 样本用固定 ID（DB-001…），必须先导入，再建自己的题目。
        self.synthetic_id = import_sample(self.repository, SAMPLE_PATH)
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        self.project_id = created["project_id"]
        self.question_id = created["brief_projection"]["questions"][0]["id"]
        self.block_id = created["report"]["blocks"][0]["id"]

    def _promote(self, url: str = "https://example.com/arm") -> tuple[str, str]:
        captured = capture_web_candidate(
            self.repository,
            self.project_id,
            url=url,
            title="Arm page",
            question_id=self.question_id,
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository, captured["candidate_id"], adapter=RichWebAdapter()
        )
        return captured["candidate_id"], promoted["source_id"]

    def test_unused_source_can_be_removed_with_its_snapshot(self) -> None:
        candidate_id, source_id = self._promote()
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        snapshot = resolve_source_snapshot_path(self.repository, dict(row))
        self.assertIsNotNone(snapshot)
        self.assertTrue(snapshot.exists())

        result = remove_source(self.repository, source_id)

        self.assertTrue(result["removed"])
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        # 受控副本也要跟着走，不能只删一行留一堆孤儿文件。
        self.assertFalse(snapshot.exists())
        with self.repository.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT id FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
            )
            candidate = connection.execute(
                "SELECT status, promoted_source_id FROM candidate_sources WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        # 那条候选退回「这轮先不用」，不是当作没见过——否则再搜一次又收回来。
        self.assertEqual(candidate["status"], "discarded")
        self.assertIsNone(candidate["promoted_source_id"])

    def test_source_with_excerpts_is_refused_and_says_why(self) -> None:
        _, source_id = self._promote()
        drafted = draft_snapshot_excerpts(
            self.repository,
            source_id,
            question_id=self.question_id,
            deliverable_block_id=self.block_id,
            adapter=ScriptedExcerptAdapter(["减速器占成本两成以上"]),
        )
        adopt_snapshot_excerpts(
            self.repository,
            source_id,
            deliverable_block_id=self.block_id,
            excerpts=drafted["excerpts"],
        )
        with self.assertRaises(SourceRemoveError) as raised:
            remove_source(self.repository, source_id)
        message = str(raised.exception)
        self.assertIn("原话", message)
        self.assertIn("追溯链", message)
        # 拒绝之后材料还在，原话也还在。
        with self.repository.connect() as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT id FROM sources WHERE id = ?", (source_id,)
                ).fetchone()
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM evidence_excerpts WHERE source_id = ?",
                    (source_id,),
                ).fetchone()[0],
                1,
            )

    def test_removing_one_source_does_not_touch_the_sample(self) -> None:
        synthetic_id = self.synthetic_id
        _, source_id = self._promote()
        with self.repository.connect() as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM sources WHERE project_id = ?", (synthetic_id,)
            ).fetchone()[0]
        remove_source(self.repository, source_id)
        with self.repository.connect() as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM sources WHERE project_id = ?", (synthetic_id,)
            ).fetchone()[0]
            text = connection.execute(
                "SELECT current_text FROM deliverable_blocks WHERE id = 'DB-001'"
            ).fetchone()["current_text"]
        self.assertEqual(before, after)
        self.assertIn("据客户提供", text)

    def test_sample_sources_that_carry_the_chain_cannot_be_removed(self) -> None:
        for source_id in ("S-002", "S-003"):
            with self.assertRaises(SourceRemoveError):
                remove_source(self.repository, source_id)

    def test_http_delete_removes_unused_source(self) -> None:
        _, source_id = self._promote()
        status, payload = dispatch_delete(self.repository, f"/sources/{source_id}")
        self.assertEqual(status, 200)
        self.assertTrue(payload["removed"])
        missing_status, missing = dispatch_delete(self.repository, "/sources/S-999")
        self.assertEqual(missing_status, 404)
        self.assertIn("不存在", missing["error"])

    def test_http_delete_refuses_a_source_that_is_in_use(self) -> None:
        _, source_id = self._promote()
        drafted = draft_snapshot_excerpts(
            self.repository,
            source_id,
            question_id=self.question_id,
            deliverable_block_id=self.block_id,
            adapter=ScriptedExcerptAdapter(["减速器占成本两成以上"]),
        )
        adopt_snapshot_excerpts(
            self.repository,
            source_id,
            deliverable_block_id=self.block_id,
            excerpts=drafted["excerpts"],
        )
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        status, payload = dispatch_delete(self.repository, f"/sources/{source_id}")
        self.assertEqual(status, 400)
        self.assertIn("不能去掉", payload["error"])


if __name__ == "__main__":
    unittest.main()
