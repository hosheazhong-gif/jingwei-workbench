from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.capture_source import capture_local_source
from app.application.export_deliverable import export_project
from app.application.import_sample import import_sample
from app.application.review_block import record_override_decision, record_review_decision
from app.projections.impact import build_impact_preview
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"
PLANTED_NUMBER = "FAKE_GDP_9999"


class SyntheticClosedLoopComparisonTest(unittest.TestCase):
    """按最小链路顺序走一遍固定样本，作为对照基线，不测耗时。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_closed_loop_keeps_trace_and_does_not_invent_numbers(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        titles = [block["title"] for block in report["blocks"]]
        self.assertEqual(titles, ["项目问题", "案例启示", "候选方向", "客户资料请求"])
        draft_before = [block["current_text"] for block in report["blocks"]]
        status_before = _claim_status(self.repository, "C-002")

        context = build_review_context(self.repository, "DB-001")
        claim = next(item for item in context["claims"] if item["id"] == "C-002")
        self.assertEqual(context["block"]["title"], "项目问题")
        self.assertEqual(claim["provenance_scope"], "client_provided")
        self.assertFalse(claim["independently_verified"])
        self.assertEqual(claim["verification_status"], "captured")
        self.assertEqual(claim["evidence"][0]["excerpt"], "60%+ 食品产业客群")
        self.assertIn("据客户提供", claim["delivery_rule"])
        self.assertIn("口径待补", claim["delivery_rule"])

        expired = self.repository.get_source("S-007")
        macro = build_impact_preview(self.repository, "S-007")
        self.assertEqual(expired["availability"], "path_expired")
        self.assertEqual(expired["title"], "中国温控物流市场总产值数据")
        self.assertEqual([item["id"] for item in macro["findings"]], ["F-003"])
        self.assertEqual(macro["claims"], [])
        self.assertEqual(macro["deliverable_blocks"], [])
        dumped = _dump(macro)
        self.assertNotIn("行业背景判断", dumped)
        self.assertNotIn("行业前景与市场表现", dumped)

        override = record_override_decision(
            self.repository,
            deliverable_block_id="DB-001",
            handling="assumption",
        )
        self.assertTrue(override["confirmation"]["verification_status_unchanged"])
        self.assertTrue(override["confirmation"]["current_text_unchanged"])
        self.assertEqual(_claim_status(self.repository, "C-002"), status_before)
        self.assertEqual(_block_texts(self.repository, self.project_id), draft_before)

        incoming = Path(self.temp_dir.name) / "replacement.txt"
        incoming.write_text(f"planted market size {PLANTED_NUMBER}\n", encoding="utf-8")
        old_hash = expired["content_hash"]
        captured = capture_local_source(
            self.repository,
            self.project_id,
            incoming,
            title="替代宏观市场材料",
            supersedes_source_id="S-007",
        )
        still_old = self.repository.get_source("S-007")
        self.assertEqual(captured["source"]["supersedes_source_id"], "S-007")
        self.assertEqual(still_old["content_hash"], old_hash)
        self.assertNotEqual(captured["source"]["id"], "S-007")
        self.assertEqual(captured["excerpt_candidates"], [])

        impact = build_impact_preview(self.repository, captured["source"]["id"])
        after_capture = build_report_projection(self.repository, self.project_id)
        self.assertEqual(
            [item["title"] for item in impact["superseded_sources"]],
            ["中国温控物流市场总产值数据"],
        )
        self.assertEqual([item["id"] for item in impact["findings"]], ["F-003"])
        self.assertEqual(impact["deliverable_blocks"], [])
        self.assertIn("不会自动改写内部稿", impact["limitation"])
        self.assertEqual(
            [block["current_text"] for block in after_capture["blocks"]],
            draft_before,
        )
        self.assertNotIn(PLANTED_NUMBER, "".join(draft_before))
        self.assertNotIn(
            PLANTED_NUMBER,
            "".join(block["current_text"] for block in after_capture["blocks"]),
        )

        record_review_decision(
            self.repository,
            "DB-001",
            action="modify",
            proposed_text=f"候选稿不得导出 {PLANTED_NUMBER}",
        )
        record_review_decision(self.repository, "DB-002", action="exclude")
        exported = export_project(self.repository, self.project_id, "markdown")
        self.assertEqual(
            exported["block_ids"],
            ["DB-001", "DB-003", "DB-004"],
        )
        self.assertIn("案例启示", exported["omitted_titles"])
        self.assertIn("据客户提供", exported["content"])
        self.assertIn("未独立核实", exported["content"])
        self.assertNotIn(PLANTED_NUMBER, exported["content"])
        self.assertNotIn("候选稿不得导出", exported["content"])
        self.assertEqual(_claim_status(self.repository, "C-002"), status_before)
        self.assertEqual(
            _block_texts(self.repository, self.project_id)[0],
            draft_before[0],
        )

        reopened = SqliteRepository(self.database_path)
        restored = build_report_projection(reopened, self.project_id)
        restored_context = build_review_context(reopened, "DB-001")
        restored_impact = build_impact_preview(reopened, captured["source"]["id"])
        self.assertEqual(
            [block["id"] for block in restored["blocks"]],
            ["DB-001", "DB-002", "DB-003", "DB-004"],
        )
        self.assertEqual(
            [block["current_text"] for block in restored["blocks"]],
            draft_before,
        )
        self.assertEqual(restored_context["latest_override"]["handling"], "assumption")
        self.assertEqual(restored_context["latest_review"]["action"], "modify")
        self.assertEqual(_claim_status(reopened, "C-002"), "captured")
        self.assertEqual([item["id"] for item in restored_impact["findings"]], ["F-003"])
        self.assertEqual(reopened.get_source("S-007")["content_hash"], old_hash)


def _block_texts(repository: SqliteRepository, project_id: str) -> list[str]:
    report = build_report_projection(repository, project_id)
    return [block["current_text"] for block in report["blocks"]]


def _claim_status(repository: SqliteRepository, claim_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT verification_status FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()["verification_status"]


def _dump(payload: object) -> str:
    return str(payload)


if __name__ == "__main__":
    unittest.main()
