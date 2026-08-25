from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_get
from app.application.capture_source import capture_local_source
from app.application.import_sample import import_sample
from app.projections.impact import build_impact_preview
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ImpactPreviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_source_claim_block_chain_uses_natural_language_and_skips_unlinked(self) -> None:
        preview = build_impact_preview(self.repository, "S-002")
        self.assertEqual(preview["source"]["title"], "项目本体分析页")
        self.assertEqual(
            [claim["id"] for claim in preview["claims"]],
            ["C-001", "C-002", "C-004", "C-005"],
        )
        self.assertTrue(all(claim["text"].strip() for claim in preview["claims"]))
        self.assertEqual(
            [finding["text"] for finding in preview["findings"]],
            ["当前材料支持客群集中与中游功能集中的工作假设，但不支持定论。"],
        )
        self.assertEqual(
            [block["title"] for block in preview["deliverable_blocks"]],
            ["项目问题"],
        )
        self.assertEqual(preview["options"], [])
        self.assertNotIn("案例启示", [block["title"] for block in preview["deliverable_blocks"]])
        self.assertNotIn("候选方向", [block["title"] for block in preview["deliverable_blocks"]])
        self.assertIn("不会自动改写内部稿", preview["limitation"])

    def test_finding_via_claims_and_block_via_finding_link(self) -> None:
        preview = build_impact_preview(self.repository, "S-003")
        self.assertEqual(preview["source"]["title"], "星河优选食品科技园案例借鉴页")
        self.assertEqual(
            [finding["id"] for finding in preview["findings"]],
            ["F-002"],
        )
        self.assertEqual(
            [block["title"] for block in preview["deliverable_blocks"]],
            ["案例启示"],
        )

    def test_macro_source_only_reports_explicit_finding_not_invented_paragraphs(self) -> None:
        preview = build_impact_preview(self.repository, "S-007")
        self.assertEqual(preview["source"]["title"], "中国温控物流市场总产值数据")
        self.assertEqual(preview["claims"], [])
        self.assertEqual(preview["options"], [])
        self.assertEqual(preview["deliverable_blocks"], [])
        self.assertEqual(
            [finding["text"] for finding in preview["findings"]],
            ["当前最严重的问题是证据链断裂，而不仅是信息量不足。"],
        )
        self.assertNotIn("行业背景判断", json.dumps(preview, ensure_ascii=False))
        self.assertNotIn("行业前景与市场表现", json.dumps(preview, ensure_ascii=False))

    def test_unlinked_source_has_empty_explicit_impact(self) -> None:
        preview = build_impact_preview(self.repository, "S-001")
        self.assertEqual(preview["claims"], [])
        self.assertEqual(preview["findings"], [])
        self.assertEqual(preview["options"], [])
        self.assertEqual(preview["deliverable_blocks"], [])

    def test_superseding_source_reuses_old_explicit_chain_without_rewriting_draft(self) -> None:
        before = build_report_projection(self.repository, self.project_id)
        incoming = Path(self.temp_dir.name) / "replacement.txt"
        incoming.write_text("replacement-macro\n", encoding="utf-8")
        captured = capture_local_source(
            self.repository,
            self.project_id,
            incoming,
            title="替代宏观市场材料",
            supersedes_source_id="S-007",
        )
        preview = build_impact_preview(self.repository, captured["source"]["id"])
        after = build_report_projection(self.repository, self.project_id)

        self.assertEqual(preview["source"]["title"], "替代宏观市场材料")
        self.assertEqual(
            [item["title"] for item in preview["superseded_sources"]],
            ["中国温控物流市场总产值数据"],
        )
        self.assertEqual(
            [finding["id"] for finding in preview["findings"]],
            ["F-003"],
        )
        self.assertEqual(
            [block["current_text"] for block in before["blocks"]],
            [block["current_text"] for block in after["blocks"]],
        )

    def test_claim_reached_only_through_excerpt_still_counts(self) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                """
                INSERT INTO evidence_excerpts (
                    id, source_id, locator_json, excerpt, schema_version,
                    created_at, updated_at
                ) VALUES (
                    'E-NEW', 'S-001', '{"kind": "note"}', '用户回忆摘录',
                    '0.2', '2026-08-13', '2026-08-13'
                )
                """
            )
            connection.execute(
                """
                INSERT INTO claims (
                    id, project_id, source_id, text, epistemic_type,
                    verification_status, schema_version, created_at, updated_at
                ) VALUES (
                    'C-NEW', 'P-DEMO-001', NULL, '仅通过摘录关联的主张',
                    'assumption', 'captured', '0.2', '2026-08-13', '2026-08-13'
                )
                """
            )
            connection.execute(
                "INSERT INTO claim_evidence VALUES ('C-NEW', 'E-NEW', 'supports')"
            )
            connection.execute(
                "INSERT INTO deliverable_block_claims VALUES ('DB-004', 'C-NEW')"
            )
        preview = build_impact_preview(self.repository, "S-001")
        self.assertEqual([claim["text"] for claim in preview["claims"]], ["仅通过摘录关联的主张"])
        self.assertEqual(
            [block["title"] for block in preview["deliverable_blocks"]],
            ["客户资料请求"],
        )

    def test_finding_propagates_to_block_only_when_explicitly_linked(self) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                "INSERT INTO deliverable_block_findings VALUES ('DB-004', 'F-003')"
            )
        preview = build_impact_preview(self.repository, "S-007")
        self.assertEqual(
            [block["title"] for block in preview["deliverable_blocks"]],
            ["客户资料请求"],
        )
        self.assertEqual([block["id"] for block in preview["deliverable_blocks"]], ["DB-004"])

    def test_missing_source_raises(self) -> None:
        with self.assertRaises(KeyError):
            build_impact_preview(self.repository, "S-missing")


class ImpactPreviewHttpTest(unittest.TestCase):
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
        status, payload = _http_json("GET", self.server.origin + "/sources/S-002/impact-preview")
        projection = build_impact_preview(self.repository, "S-002")
        dispatched = dispatch_get(self.repository, "/sources/S-002/impact-preview")
        self.assertEqual(status, 200)
        self.assertEqual(payload, projection)
        self.assertEqual(dispatched, (200, projection))
        self.assertEqual(payload["deliverable_blocks"][0]["title"], "项目问题")

    def test_missing_source_and_write_are_rejected(self) -> None:
        missing_status, missing_payload = _http_json(
            "GET", self.server.origin + "/sources/missing/impact-preview"
        )
        write_status, write_payload = _http_json(
            "POST", self.server.origin + "/sources/S-007/impact-preview"
        )
        self.assertEqual(missing_status, 404)
        self.assertEqual(write_status, 405)
        self.assertIn("error", missing_payload)
        self.assertIn("只读", write_payload["error"])


def _http_json(method: str, url: str) -> tuple[int, dict]:
    request = Request(url, method=method)
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        payload = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, payload


if __name__ == "__main__":
    unittest.main()
