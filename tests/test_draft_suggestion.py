from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.local_source import sha256_file
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.attach_claim import attach_claim_to_block, unlink_claim_from_block
from app.application.candidate_source import (
    capture_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.create_project import create_project
from app.application.draft_suggestion import (
    DraftSuggestionError,
    adopt_model_suggestion,
    dismiss_model_suggestion,
    draft_block_revision,
    draft_model_suggestions,
)
from app.application.import_sample import import_sample
from app.application.question_progress import defer_research_question
from app.application.review_block import adopt_revision
from app.projections.report import build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ScriptedDraftAdapter:
    key = "scripted"

    def propose(self, context):
        return [
            {"kind": "finding", "text": "现有材料只支持缺口判断，不能定论。"},
            {"kind": "option", "text": "先补租户结构，再讨论改造必要性。"},
        ]


class ScriptedRevisionAdapter:
    key = "scripted-revision"

    def propose(self, context):
        self.context = context
        return [{"kind": "revision", "text": "现有材料只支持缺口判断，口径仍待补。"}]


class WallRevisionAdapter:
    key = "wall-revision"

    def propose(self, context):
        return [
            {
                "kind": "revision",
                "text": "第一句。第二句。第三句。第四句。",
            }
        ]


class MemoryWebAdapter:
    key = "web_page"

    def snapshot(self, url: str, project_files: Path) -> dict:
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / "snapshot.bin"
        destination.write_bytes(
            "<html><body><p>opened</p>"
            "<p>这份假快照要有一段够长的正文，工作台才认它存下了可读内容，"
            "否则「看快照 / 从快照扒原话」两个键都不该出现。</p>"
            "</body></html>".encode("utf-8")
        )
        return {
            "file_name": "snapshot.bin",
            "original_url": url,
            "snapshot_path": destination,
            "content_hash": sha256_file(destination),
            "availability": "available",
        }


class DraftSuggestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_unconfigured_adapter_refuses_to_invent(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        with patch.dict(os.environ, {"JINGWEI_DRAFT_API_KEY": ""}, clear=False):
            with self.assertRaises(DraftSuggestionError) as raised:
                draft_model_suggestions(self.repository, "DB-001")
        self.assertIn("还没接模型", str(raised.exception))
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertEqual(after["suggestions"], [])

    def test_scripted_draft_does_not_rewrite_synthetic(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        result = draft_model_suggestions(
            self.repository, "DB-001", adapter=ScriptedDraftAdapter()
        )
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(len(result["suggestion_ids"]), 2)
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertEqual(len(after["suggestions"]), 2)
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["verification_status_unchanged"])
        self.assertIn("还没有挂到段落", result["confirmation"]["message"])

    def test_adopt_writes_finding_not_draft_or_verification(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        drafted = draft_model_suggestions(
            self.repository, "DB-001", adapter=ScriptedDraftAdapter()
        )
        finding_id = drafted["suggestion_ids"][0]
        result = adopt_model_suggestion(self.repository, finding_id)
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertTrue(
            any(
                item["text"] == "现有材料只支持缺口判断，不能定论。"
                for item in after["findings"]
            )
        )
        self.assertEqual(result["finding_id"], after["findings"][-1]["id"])
        self.assertNotIn(finding_id, [item["id"] for item in after["suggestions"]])

    def test_dismiss_keeps_draft_and_verification(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        drafted = draft_model_suggestions(
            self.repository, "DB-001", adapter=ScriptedDraftAdapter()
        )
        dismissed = dismiss_model_suggestion(self.repository, drafted["suggestion_ids"][1])
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(len(after["suggestions"]), 1)
        self.assertIn("这版先不用", dismissed["confirmation"]["message"])

    def test_blank_project_can_draft_without_touching_synthetic(self) -> None:
        created = create_project(
            self.repository,
            name="先拟空白题",
            original_context="客户只给了一句话。",
        )
        block_id = created["report"]["blocks"][0]["id"]
        synthetic_before = build_review_context(self.repository, "DB-001")
        draft_model_suggestions(
            self.repository, block_id, adapter=ScriptedDraftAdapter()
        )
        adopt_model_suggestion(
            self.repository,
            build_review_context(self.repository, block_id)["suggestions"][0]["id"],
        )
        synthetic_after = build_review_context(self.repository, "DB-001")
        self.assertEqual(
            synthetic_after["block"]["current_text"], synthetic_before["block"]["current_text"]
        )

    def test_revision_draft_does_not_replace_current_text(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        adapter = ScriptedRevisionAdapter()
        result = draft_block_revision(
            self.repository, "DB-001", adapter=adapter
        )
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(adapter.context["task"], "revision")
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertEqual(len(after["pending_revisions"]), 1)
        self.assertEqual(
            after["pending_revisions"][0]["body"],
            "现有材料只支持缺口判断，口径仍待补。",
        )
        self.assertTrue(result["confirmation"]["current_text_unchanged"])
        self.assertTrue(result["confirmation"]["model_drafted"])
        self.assertIn("收下后才进给经理的稿", result["confirmation"]["message"])
        self.assertEqual(after["suggestions"], before["suggestions"])
        self.assertIn(
            "当前项目的租户、空间与温控物流设施结构到底是什么？",
            adapter.context["questions"],
        )
        self.assertEqual(adapter.context["focus_question"], "")
        self.assertTrue(adapter.context["materials"])
        self.assertTrue(adapter.context["excerpts"])
        self.assertFalse(adapter.context["placeholder"])
        self.assertIn("60%+ 食品产业客群", adapter.context["excerpts"])
        self.assertTrue(
            any(
                "60%+ 食品产业客群" in line and "客户提供" in line
                for line in adapter.context.get("excerpt_lines") or []
            )
        )
        self.assertNotIn(
            "60%+ 食品产业客群", adapter.context.get("other_excerpts") or []
        )
        self.assertTrue(adapter.context["original_context"])

    def test_revision_draft_splits_sentence_wall_before_saving_candidate(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        result = draft_block_revision(
            self.repository, "DB-001", adapter=WallRevisionAdapter()
        )
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            after["pending_revisions"][0]["body"],
            "第一句。\n\n第二句。\n\n第三句。\n\n第四句。",
        )
        self.assertTrue(result["confirmation"]["current_text_unchanged"])

    def test_placeholder_section_is_flagged_for_gap_draft(self) -> None:
        created = create_project(
            self.repository,
            name="缺口空白题",
            original_context="客户只给了一句话，还没有项目本体材料。",
        )
        block_id = created["report"]["blocks"][0]["id"]
        adapter = ScriptedRevisionAdapter()
        draft_block_revision(self.repository, block_id, adapter=adapter)
        self.assertTrue(adapter.context["placeholder"])
        self.assertEqual(
            adapter.context["original_context"],
            "客户只给了一句话，还没有项目本体材料。",
        )
        self.assertEqual(adapter.context["current_text"].strip(), "这一节还没写。")
        self.assertEqual(adapter.context["excerpts"], [])

    def test_web_page_excerpt_is_not_labeled_client_provided(self) -> None:
        created = create_project(
            self.repository,
            name="网页挂摘录",
            original_context="先打开官网再写缺口。",
        )
        project_id = created["project_id"]
        block_id = created["report"]["blocks"][0]["id"]
        captured = capture_web_candidate(
            self.repository,
            project_id,
            url="https://example.com/park",
            title="园区官网",
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=MemoryWebAdapter(),
        )
        attach_claim_to_block(
            self.repository,
            block_id,
            source_id=promoted["source_id"],
            excerpt="园区名称",
            text="园区官网标题写了园区名称。",
        )
        adapter = ScriptedRevisionAdapter()
        draft_block_revision(self.repository, block_id, adapter=adapter)
        lines = adapter.context.get("excerpt_lines") or []
        self.assertTrue(
            any(
                "园区名称" in line
                and "公开网页" in line
                and "不是客户提供" in line
                for line in lines
            )
        )
        self.assertFalse(any("据客户提供" in line for line in lines))
        self.assertFalse(any("客户提供，口径待补" in line for line in lines))
        self.assertTrue(
            any("公开网页，不是客户提供" in item for item in adapter.context["materials"])
        )
        review = build_review_context(self.repository, block_id)
        self.assertEqual(review["claims"][0]["source"]["kind"], "web_page")

    def test_unlinked_excerpt_is_not_this_section_evidence(self) -> None:
        unlink_claim_from_block(self.repository, "DB-001", "C-002")
        adapter = ScriptedRevisionAdapter()
        draft_block_revision(self.repository, "DB-001", adapter=adapter)
        self.assertNotIn("60%+ 食品产业客群", adapter.context["excerpts"])
        self.assertIn("60%+ 食品产业客群", adapter.context["other_excerpts"])
        self.assertTrue(
            any(
                "60%+ 食品产业客群" in line and "客户提供" in line
                for line in adapter.context.get("other_excerpt_lines") or []
            )
        )
        self.assertTrue(adapter.context["materials"])

    def test_revision_draft_focuses_selected_question_and_skips_deferred(self) -> None:
        defer_research_question(self.repository, "RQ-06")
        adapter = ScriptedRevisionAdapter()
        draft_block_revision(
            self.repository,
            "DB-001",
            adapter=adapter,
            question_id="RQ-02",
        )
        self.assertEqual(
            adapter.context["focus_question"],
            "客群单一及食品产业占比60%+的口径和原始数据是什么？",
        )
        self.assertEqual(
            adapter.context["enough_for_now"],
            "能回到租户清单、面积或收入分母",
        )
        self.assertEqual(
            adapter.context["questions"][0],
            adapter.context["focus_question"],
        )
        self.assertNotIn(
            "候选方向的空间、合规、投资和运营门槛是什么？",
            adapter.context["questions"],
        )
        with self.assertRaises(DraftSuggestionError):
            draft_block_revision(
                self.repository,
                "DB-001",
                adapter=ScriptedRevisionAdapter(),
                question_id="RQ-06",
            )
        with self.assertRaises(DraftSuggestionError):
            draft_block_revision(
                self.repository,
                "DB-001",
                adapter=ScriptedRevisionAdapter(),
                question_id="RQ-99",
            )

    def test_adopting_revision_draft_replaces_text_not_verification(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        other_before = build_review_context(self.repository, "DB-002")
        drafted = draft_block_revision(
            self.repository, "DB-001", adapter=ScriptedRevisionAdapter()
        )
        version = drafted["pending_revision"]["version"]
        adopted = adopt_revision(self.repository, "DB-001", version)
        after = build_review_context(self.repository, "DB-001")
        other_after = build_review_context(self.repository, "DB-002")
        self.assertEqual(
            after["block"]["current_text"],
            "现有材料只支持缺口判断，口径仍待补。",
        )
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertFalse(adopted["confirmation"]["current_text_unchanged"])
        self.assertEqual(
            other_after["block"]["current_text"], other_before["block"]["current_text"]
        )


class DraftSuggestionHttpTest(unittest.TestCase):
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

    def test_http_unconfigured_does_not_write_draft(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        with patch.dict(os.environ, {"JINGWEI_DRAFT_API_KEY": ""}, clear=False):
            status, payload = self._post("/deliverable-blocks/DB-001/model-suggestions", {})
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(status, 400)
        self.assertIn("还没接模型", payload["error"])
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(after["suggestions"], [])

    def test_http_unconfigured_draft_revision_does_not_write_draft(self) -> None:
        before = build_review_context(self.repository, "DB-001")
        with patch.dict(os.environ, {"JINGWEI_DRAFT_API_KEY": ""}, clear=False):
            status, payload = self._post("/deliverable-blocks/DB-001/draft-revision", {})
        after = build_review_context(self.repository, "DB-001")
        self.assertEqual(status, 400)
        self.assertIn("还没接模型", payload["error"])
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(after["pending_revisions"], before["pending_revisions"])

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


if __name__ == "__main__":
    unittest.main()
