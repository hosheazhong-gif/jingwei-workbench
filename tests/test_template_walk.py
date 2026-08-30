from __future__ import annotations

import base64
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.attach_claim import attach_claim_to_block
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.export_deliverable import export_project
from app.application.review_block import (
    adopt_revision,
    propose_block_revision,
    record_review_decision,
)
from app.application.round_questions import adopt_round_questions, draft_round_questions
from app.application.verify_claim import update_claim_verification
from app.projections.report import build_review_context
from app.projections.templates import build_template_list_projection


EVIDENCE = "验收材料原话：公开资料只支持本轮的事实边界，尚不足以形成最终建议。"
DRAFT = "一、本轮事实边界\n1. 公开资料只支持本轮的事实边界。（出处：验收材料）\n\n小结\n本轮不形成最终建议。"


class FirstTemplateHintAdapter:
    """用模板第一条提示生成确定性问题；不调用外部模型。"""

    def __init__(self) -> None:
        self.context: dict | None = None

    def propose(self, context: dict) -> list[dict[str, str]]:
        self.context = context
        return [
            {
                "question": context["template_hints"][0],
                "label": "模板验收问题",
                "section": context["sections"][0],
                "enough_for_now": "有一条可追溯原话并形成一节可导出的稿。",
            }
        ]


class EveryTemplateEndToEndTest(unittest.TestCase):
    def test_every_selectable_template_completes_the_same_controlled_loop(self) -> None:
        templates = build_template_list_projection()["templates"]
        # 不写死数字：每个正式模板都要过这条闭环，有几个就跑几个。
        self.assertGreaterEqual(len(templates), 2)

        for template in templates:
            with self.subTest(template=template["key"]), tempfile.TemporaryDirectory() as root:
                repository = SqliteRepository(Path(root) / "jingwei.sqlite3")
                repository.migrate()
                created = create_project(
                    repository,
                    name="模板端到端验收：" + template["name"],
                    original_context="经理说：先把公开材料能支持到哪写清楚。",
                    template_key=template["key"],
                )
                project_id = created["project_id"]
                block_id = created["report"]["blocks"][0]["id"]
                self.assertEqual(
                    created["report"]["project"]["template_key"], template["key"]
                )

                adapter = FirstTemplateHintAdapter()
                drafted = draft_round_questions(
                    repository, project_id, adapter=adapter
                )
                self.assertEqual(
                    adapter.context["template_hints"], template["question_labels"]
                )
                adopted = adopt_round_questions(
                    repository, project_id, drafted["questions"]
                )
                question_id = adopted["question_ids"][0]
                question = next(
                    row
                    for row in adopted["brief_projection"]["questions"]
                    if row["id"] == question_id
                )
                self.assertEqual(question["target_block_id"], block_id)

                captured = capture_local_source(
                    repository,
                    project_id,
                    title="验收材料",
                    uploaded_name="evidence.txt",
                    uploaded_bytes=EVIDENCE.encode("utf-8"),
                    question_id=question_id,
                )
                source_id = captured["source"]["id"]
                attached = attach_claim_to_block(
                    repository,
                    block_id,
                    source_id=source_id,
                    excerpt=EVIDENCE,
                    text=EVIDENCE,
                    locator_kind="snapshot",
                )
                claim_id = attached["claim_id"]

                proposed = propose_block_revision(repository, block_id, body=DRAFT)
                version = proposed["pending_revision"]["version"]
                adopt_revision(repository, block_id, version)
                update_claim_verification(
                    repository,
                    block_id,
                    claim_id,
                    verification_status="source_checked",
                )
                record_review_decision(repository, block_id, action="approve")

                review = build_review_context(repository, block_id)
                claim = next(row for row in review["claims"] if row["id"] == claim_id)
                self.assertEqual(claim["verification_status"], "source_checked")
                self.assertFalse(claim["independently_verified"])
                self.assertEqual(review["block"]["current_text"], DRAFT)

                exported = export_project(repository, project_id, "word")
                self.assertIn(block_id, exported["block_ids"])
                package = base64.b64decode(exported["content"])
                self.assertTrue(zipfile.is_zipfile(BytesIO(package)))


if __name__ == "__main__":
    unittest.main()
