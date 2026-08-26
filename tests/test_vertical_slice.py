from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.import_sample import SampleImportError, import_sample
from app.domain import EpistemicType, OptionStatus
from app.ports.contracts import AnalysisModule, DeliverableExporter, Parser
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"
MIGRATION_V01 = PROJECT_ROOT / "app/migrations/0001_schema_v0_1.sql"
OPTION_STATUSES = {status.value for status in OptionStatus}


class VerticalSliceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_schema_migration_and_sample_import(self) -> None:
        with self.repository.connect() as connection:
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            versions = {
                row["schema_version"]
                for row in connection.execute(
                    "SELECT schema_version FROM schema_migrations"
                )
            }
            project = connection.execute(
                "SELECT template_key, execution_strategy_key, schema_version FROM projects"
            ).fetchone()
        self.assertEqual(migration_count, 9)
        self.assertEqual(
            versions, {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"}
        )
        # 0008 是数据迁移：旧库里绑着单个案子名的 template_key 被改成通用 key，
        # 题目名称、稿、主张核验状态、来源哈希都不动（PRD 20.6）。
        self.assertEqual(
            project["template_key"], "industry_chain_analysis_presales"
        )
        self.assertEqual(project["execution_strategy_key"], "fixed_workflow")
        self.assertEqual(project["schema_version"], "0.8")

    def test_migration_is_idempotent(self) -> None:
        self.repository.migrate()
        with self.repository.connect() as connection:
            migration_count = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
        self.assertEqual(migration_count, 9)

    def test_report_and_review_share_the_same_object_ids(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        block_ids = {block["id"] for block in report["blocks"]}
        context = build_review_context(self.repository, "DB-001")
        self.assertEqual(block_ids, {"DB-001", "DB-002", "DB-003", "DB-004"})
        self.assertIn(context["block"]["id"], block_ids)
        self.assertEqual(
            {claim["id"] for claim in context["claims"]},
            {"C-001", "C-002", "C-004", "C-005"},
        )

    def test_report_returns_actual_block_text_for_db001_to_db004(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        texts = {block["id"]: block["current_text"] for block in report["blocks"]}
        self.assertIn("生产型租户占比可以作为客户提供信息进入报告", texts["DB-001"])
        self.assertIn("尚未完成验证", texts["DB-002"])
        self.assertIn("它们是研究假设，不是推荐方案", texts["DB-003"])
        self.assertIn("脱敏租户、面积、租金、合同期限和招商线索台账", texts["DB-004"])
        for block_id, text in texts.items():
            self.assertTrue(text.strip(), f"{block_id} 缺少正文")

    def test_client_provided_claim_keeps_delivery_rule_and_verification_limit(self) -> None:
        context = build_review_context(self.repository, "DB-001")
        claim = next(item for item in context["claims"] if item["id"] == "C-002")
        self.assertEqual(claim["provenance_scope"], "client_provided")
        self.assertFalse(claim["independently_verified"])
        self.assertEqual(claim["verification_status"], "captured")
        self.assertEqual(claim["epistemic_type"], EpistemicType.FACTUAL_CLAIM)
        self.assertIn("据客户提供", claim["delivery_rule"])
        self.assertEqual(claim["evidence"][0]["excerpt"], "60%+ 食品产业客群")
        self.assertIn("口径待补", claim["delivery_rule"])

    def test_deliverable_block_finding_and_option_links(self) -> None:
        report = build_report_projection(self.repository, self.project_id)
        by_id = {block["id"]: block for block in report["blocks"]}
        self.assertEqual(by_id["DB-001"]["claim_ids"], ["C-001", "C-002", "C-004", "C-005"])
        self.assertEqual(by_id["DB-001"]["finding_ids"], [])
        self.assertEqual(by_id["DB-001"]["option_ids"], [])
        self.assertEqual(by_id["DB-002"]["finding_ids"], ["F-002"])
        self.assertEqual(by_id["DB-003"]["option_ids"], ["O-001", "O-002", "O-003"])
        self.assertEqual(by_id["DB-003"]["claim_ids"], [])
        self.assertEqual(by_id["DB-004"]["claim_ids"], [])
        self.assertEqual(by_id["DB-004"]["finding_ids"], [])
        self.assertEqual(by_id["DB-004"]["option_ids"], [])

    def test_option_status_is_not_hypothesis(self) -> None:
        with self.repository.connect() as connection:
            statuses = {
                row["status"]
                for row in connection.execute("SELECT status FROM options")
            }
        self.assertTrue(statuses)
        self.assertNotIn("hypothesis", statuses)
        self.assertTrue(statuses <= OPTION_STATUSES)
        self.assertEqual(statuses, {"candidate"})

    def test_import_never_silently_overwrites_a_project(self) -> None:
        with self.assertRaises(SampleImportError):
            import_sample(self.repository, SAMPLE_PATH)

    def test_foreign_keys_are_enforced(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO deliverable_block_claims
                    (deliverable_block_id, claim_id) VALUES ('DB-001', 'missing')
                    """
                )

    def test_override_does_not_change_claim_verification_status(self) -> None:
        with self.repository.connect() as connection:
            statuses = {
                row["verification_status"]
                for row in connection.execute("SELECT verification_status FROM claims")
            }
            override = connection.execute(
                """
                SELECT handling, reason, deliverable_block_id, schema_version
                FROM override_decisions WHERE id = 'OVR-001'
                """
            ).fetchone()
        self.assertEqual(statuses, {"captured"})
        self.assertEqual(override["handling"], "assumption")
        self.assertIsNone(override["deliverable_block_id"])
        self.assertEqual(override["schema_version"], "0.8")

    def test_paragraph_override_does_not_change_claim_verification_status(self) -> None:
        with self.repository.transaction() as connection:
            connection.execute(
                """
                INSERT INTO override_decisions (
                    id, project_id, deliverable_block_id, handling, reason,
                    review_trigger, target_version, created_at, schema_version, updated_at
                ) VALUES (
                    'OVR-DB001', ?, 'DB-001', 'scenario', '段落按情景表达',
                    '补料后重审', 1, '2026-08-13', '0.2', '2026-08-13'
                )
                """,
                (self.project_id,),
            )
        with self.repository.connect() as connection:
            statuses = {
                row["verification_status"]
                for row in connection.execute("SELECT verification_status FROM claims")
            }
            row = connection.execute(
                "SELECT deliverable_block_id, handling FROM override_decisions WHERE id = 'OVR-DB001'"
            ).fetchone()
        self.assertEqual(statuses, {"captured"})
        self.assertEqual(row["deliverable_block_id"], "DB-001")
        self.assertEqual(row["handling"], "scenario")

    def test_claim_hypothesis_is_rejected_after_v0_2(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO claims (
                        id, project_id, text, epistemic_type, verification_status,
                        schema_version, created_at, updated_at
                    ) VALUES (
                        'C-BAD', ?, 'x', 'hypothesis', 'captured', '0.2',
                        '2026-08-13', '2026-08-13'
                    )
                    """,
                    (self.project_id,),
                )

    def test_option_hypothesis_is_rejected_after_v0_2(self) -> None:
        with self.assertRaises(sqlite3.IntegrityError):
            with self.repository.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO options (
                        id, project_id, text, status, schema_version, created_at, updated_at
                    ) VALUES (
                        'O-BAD', ?, 'x', 'hypothesis', '0.2', '2026-08-13', '2026-08-13'
                    )
                    """,
                    (self.project_id,),
                )

    def test_sample_snapshot_paths_are_relative_and_portable(self) -> None:
        data = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        snapshot_paths = [
            source["snapshot_path"]
            for source in data["sources"]
            if source.get("snapshot_path")
        ]
        self.assertTrue(snapshot_paths)
        for snapshot_path in snapshot_paths:
            relative_path = Path(snapshot_path)
            self.assertFalse(relative_path.is_absolute())
            self.assertTrue((PROJECT_ROOT / relative_path).is_file())

    def test_parser_and_analysis_module_seams_exist(self) -> None:
        self.assertTrue(hasattr(AnalysisModule, "recommended_question_labels"))
        self.assertTrue(hasattr(Parser, "parse"))
        self.assertTrue(hasattr(DeliverableExporter, "export"))

    def test_core_entities_have_version_and_audit_columns(self) -> None:
        required = {"schema_version", "created_at", "updated_at"}
        with self.repository.connect() as connection:
            for table in (
                "projects",
                "briefs",
                "research_questions",
                "sources",
                "evidence_excerpts",
                "claims",
                "findings",
                "options",
                "deliverable_blocks",
                "review_decisions",
                "override_decisions",
                "candidate_sources",
                "model_suggestions",
            ):
                columns = {
                    row["name"]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                self.assertTrue(
                    required <= columns,
                    f"{table} missing {required - columns}",
                )
            source_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(sources)")
            }
        self.assertTrue(
            {
                "institution",
                "published_at",
                "original_url",
                "original_path",
                "permission",
                "sensitivity",
                "source_quality",
            }
            <= source_columns
        )

    def test_database_file_can_be_deleted_after_use(self) -> None:
        extra_path = Path(self.temp_dir.name) / "release-check.sqlite3"
        repository = SqliteRepository(extra_path)
        repository.migrate()
        project_id = import_sample(repository, SAMPLE_PATH)
        build_report_projection(repository, project_id)
        build_review_context(repository, "DB-001")
        extra_path.unlink()
        self.assertFalse(extra_path.exists())


class SchemaMigrationV01ToV02Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "legacy.sqlite3"

    def test_v0_1_data_survives_migration_without_silent_rewrite(self) -> None:
        _seed_v0_1_database(self.database_path)
        repository = SqliteRepository(self.database_path)
        repository.migrate()

        with repository.connect() as connection:
            claim = connection.execute(
                "SELECT text, epistemic_type, verification_status FROM claims WHERE id = 'C-OLD'"
            ).fetchone()
            option = connection.execute(
                "SELECT text, status FROM options WHERE id = 'O-OLD'"
            ).fetchone()
            source = connection.execute(
                "SELECT snapshot_path, content_hash, title FROM sources WHERE id = 'S-OLD'"
            ).fetchone()
            block = connection.execute(
                "SELECT current_text, title FROM deliverable_blocks WHERE id = 'DB-OLD'"
            ).fetchone()
            review = connection.execute(
                "SELECT action, reason, schema_version FROM review_decisions WHERE id = 'RV-OLD'"
            ).fetchone()
            override = connection.execute(
                """
                SELECT handling, reason, deliverable_block_id, schema_version
                FROM override_decisions WHERE id = 'OVR-OLD'
                """
            ).fetchone()
            revision = connection.execute(
                """
                SELECT body, origin, adopted FROM deliverable_block_revisions
                WHERE deliverable_block_id = 'DB-OLD' AND version = 1
                """
            ).fetchone()
            versions = {
                row["schema_version"]
                for row in connection.execute("SELECT schema_version FROM schema_migrations")
            }
            hypothesis_claims = connection.execute(
                "SELECT COUNT(*) FROM claims WHERE epistemic_type = 'hypothesis'"
            ).fetchone()[0]
            hypothesis_options = connection.execute(
                "SELECT COUNT(*) FROM options WHERE status = 'hypothesis'"
            ).fetchone()[0]
            suggestion_count = connection.execute(
                "SELECT COUNT(*) FROM model_suggestions"
            ).fetchone()[0]

        self.assertEqual(claim["text"], "旧主张正文不得丢失")
        self.assertEqual(claim["epistemic_type"], "assumption")
        self.assertEqual(claim["verification_status"], "captured")
        self.assertEqual(option["text"], "旧方向正文不得丢失")
        self.assertEqual(option["status"], "candidate")
        self.assertEqual(source["content_hash"], "abc123hash")
        self.assertEqual(source["snapshot_path"], "samples/legacy.txt")
        self.assertEqual(source["title"], "历史来源")
        self.assertEqual(block["current_text"], "人工正文不得因迁移丢失")
        self.assertEqual(revision["body"], "人工正文不得因迁移丢失")
        self.assertEqual(revision["origin"], "snapshot")
        self.assertEqual(revision["adopted"], 1)
        self.assertEqual(review["action"], "modify")
        self.assertEqual(review["reason"], "保留人工修改理由")
        self.assertEqual(review["schema_version"], "0.2")
        self.assertEqual(override["handling"], "assumption")
        self.assertEqual(override["reason"], "资料不足时生成内部版本")
        self.assertIsNone(override["deliverable_block_id"])
        self.assertEqual(
            versions, {"0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8"}
        )
        self.assertEqual(hypothesis_claims, 0)
        self.assertEqual(hypothesis_options, 0)
        self.assertEqual(suggestion_count, 0)

    def test_legacy_database_file_can_be_deleted_after_migration(self) -> None:
        _seed_v0_1_database(self.database_path)
        repository = SqliteRepository(self.database_path)
        repository.migrate()
        with repository.connect() as connection:
            connection.execute("SELECT COUNT(*) FROM claims").fetchone()
        self.database_path.unlink()
        self.assertFalse(self.database_path.exists())


def _seed_v0_1_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(MIGRATION_V01.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO schema_migrations (migration_id, schema_version, applied_at)
            VALUES ('0001_schema_v0_1', '0.1', '2026-08-12T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO projects (
                id, name, template_key, execution_strategy_key, schema_version,
                created_at, updated_at
            ) VALUES (
                'P-OLD', '旧项目', 'case_specific_low_info_presales', 'fixed_workflow',
                '0.1', '2026-08-12', '2026-08-12'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO sources (
                id, project_id, kind, title, availability, snapshot_path,
                content_hash, schema_version
            ) VALUES (
                'S-OLD', 'P-OLD', 'user_provided', '历史来源', 'available',
                'samples/legacy.txt', 'abc123hash', '0.1'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO evidence_excerpts (
                id, source_id, locator_json, excerpt, schema_version
            ) VALUES ('E-OLD', 'S-OLD', '{"kind": "page"}', '摘录', '0.1')
            """
        )
        connection.execute(
            """
            INSERT INTO claims (
                id, project_id, source_id, text, epistemic_type,
                verification_status, schema_version
            ) VALUES (
                'C-OLD', 'P-OLD', 'S-OLD', '旧主张正文不得丢失',
                'hypothesis', 'captured', '0.1'
            )
            """
        )
        connection.execute(
            "INSERT INTO claim_evidence VALUES ('C-OLD', 'E-OLD', 'supports')"
        )
        connection.execute(
            """
            INSERT INTO options (id, project_id, text, status, schema_version)
            VALUES ('O-OLD', 'P-OLD', '旧方向正文不得丢失', 'hypothesis', '0.1')
            """
        )
        connection.execute(
            """
            INSERT INTO deliverable_blocks (
                id, project_id, title, current_text, delivery_status,
                current_version, schema_version
            ) VALUES (
                'DB-OLD', 'P-OLD', '旧段落', '人工正文不得因迁移丢失',
                'draft', 1, '0.1'
            )
            """
        )
        connection.execute(
            "INSERT INTO deliverable_block_claims VALUES ('DB-OLD', 'C-OLD')"
        )
        connection.execute(
            """
            INSERT INTO review_decisions (
                id, deliverable_block_id, action, reason, actor, created_at, target_version
            ) VALUES (
                'RV-OLD', 'DB-OLD', 'modify', '保留人工修改理由', 'analyst',
                '2026-08-12', 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO override_decisions (
                id, project_id, deliverable_block_id, handling, reason,
                review_trigger, target_version, created_at
            ) VALUES (
                'OVR-OLD', 'P-OLD', NULL, 'assumption', '资料不足时生成内部版本',
                '补料后重审', 1, '2026-08-12'
            )
            """
        )
        connection.commit()
    finally:
        connection.close()


if __name__ == "__main__":
    unittest.main()
