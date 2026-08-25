from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from app.adapters.http_draft import (
    DEFAULT_DRAFT_URL,
    HttpJsonDraftAdapter,
    parse_draft_proposals,
    resolve_draft_adapter,
    test_draft_connection,
    _REVISION_PROMPT,
    _user_prompt,
)
from app.adapters.sqlite_repository import SqliteRepository
from app.adapters.unconfigured_draft import UnconfiguredDraftAdapter
from app.application.draft_suggestion import DraftSuggestionError, draft_model_suggestions
from app.application.import_sample import import_sample
from app.projections.report import build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self, limit: int = -1) -> bytes:
        if limit < 0:
            return self._body
        return self._body[:limit]

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class HttpDraftAdapterTest(unittest.TestCase):
    def test_connection_check_makes_minimal_request_without_returning_key(self) -> None:
        captured: dict = {}

        def opener(request, timeout=0):
            captured["authorization"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse({"choices": [{"message": {"content": "OK"}}]})

        result = test_draft_connection(
            environ={
                "JINGWEI_DRAFT_PROVIDER": "deepseek",
                "JINGWEI_DRAFT_API_KEY": "sk-connection-test",
            },
            opener=opener,
        )
        self.assertTrue(result["connected"])
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(captured["body"]["max_tokens"], 8)
        self.assertEqual(captured["authorization"], "Bearer sk-connection-test")
        self.assertNotIn("sk-connection-test", repr(result))

    def test_resolve_without_key_stays_unconfigured(self) -> None:
        adapter = resolve_draft_adapter(environ={})
        self.assertIsInstance(adapter, UnconfiguredDraftAdapter)

    def test_resolve_with_key_uses_http_adapter(self) -> None:
        adapter = resolve_draft_adapter(
            environ={
                "JINGWEI_DRAFT_API_KEY": "test-key",
                "JINGWEI_DRAFT_MODEL": "demo-model",
            }
        )
        self.assertIsInstance(adapter, HttpJsonDraftAdapter)
        self.assertEqual(adapter.key, "http_openai")

    def test_deepseek_preset_uses_deepseek_endpoint(self) -> None:
        adapter = resolve_draft_adapter(
            environ={
                "JINGWEI_DRAFT_API_KEY": "test-key",
                "JINGWEI_DRAFT_PROVIDER": "deepseek",
            }
        )
        self.assertIsInstance(adapter, HttpJsonDraftAdapter)
        self.assertEqual(adapter.key, "http_deepseek")
        self.assertEqual(adapter._url, "https://api.deepseek.com/v1/chat/completions")
        self.assertEqual(adapter._model, "deepseek-chat")

    def test_kimi_alias_and_url_override(self) -> None:
        adapter = resolve_draft_adapter(
            environ={
                "JINGWEI_DRAFT_API_KEY": "test-key",
                "JINGWEI_DRAFT_PROVIDER": "kimi",
                "JINGWEI_DRAFT_URL": "https://example.com/v1/chat/completions",
                "JINGWEI_DRAFT_MODEL": "moonshot-v1-32k",
            }
        )
        self.assertEqual(adapter.key, "http_moonshot")
        self.assertEqual(adapter._url, "https://example.com/v1/chat/completions")
        self.assertEqual(adapter._model, "moonshot-v1-32k")

    def test_unknown_provider_refuses_without_writing(self) -> None:
        with self.assertRaises(DraftSuggestionError) as raised:
            resolve_draft_adapter(
                environ={
                    "JINGWEI_DRAFT_API_KEY": "test-key",
                    "JINGWEI_DRAFT_PROVIDER": "unknown-lab",
                }
            )
        self.assertIn("还不认识这个模型服务", str(raised.exception))
        self.assertIn("deepseek", str(raised.exception))
        self.assertIn("没有改内部稿", str(raised.exception))

    def test_user_prompt_puts_focus_question_before_the_list(self) -> None:
        prompt = _user_prompt(
            {
                "title": "缺口",
                "decision_question": "还值不值得跟",
                "focus_question": "租户结构清不清",
                "enough_for_now": "能回到分母和时点",
                "questions": ["租户结构清不清", "门槛是什么"],
                "materials": ["客户口头纪要"],
                "current_text": "现有材料只支持缺口判断。",
                "excerpts": ["这一节挂上的原话"],
                "other_excerpts": ["别处的原话"],
                "claims": [],
            }
        )
        self.assertIn("这一节先回答：租户结构清不清", prompt)
        self.assertIn("这条何时够用：能回到分母和时点", prompt)
        self.assertIn("本轮问题：", prompt)
        self.assertLess(
            prompt.index("这一节先回答"),
            prompt.index("本轮问题："),
        )
        self.assertIn("材料匣：", prompt)
        self.assertIn("客户口头纪要", prompt)
        self.assertIn("这一节已挂原话：", prompt)
        self.assertIn("这一节挂上的原话", prompt)
        self.assertIn("未挂到这一节", prompt)
        self.assertIn("别处的原话", prompt)
        self.assertNotIn("已有摘录", prompt)
        self.assertIn("不得写成客户提供", _REVISION_PROMPT)
        self.assertIn("不能把网页材料说成客户口头", _REVISION_PROMPT)

    def test_labeled_web_excerpt_is_not_sent_as_client_provided(self) -> None:
        prompt = _user_prompt(
            {
                "title": "缺口",
                "decision_question": "还值不值得跟",
                "excerpts": ["园区名称"],
                "excerpt_lines": [
                    "「园区名称」——公开网页，不是客户提供，未独立核实。出处：园区官网"
                ],
                "claims": [],
            }
        )
        self.assertIn("公开网页，不是客户提供", prompt)
        self.assertIn("园区名称", prompt)
        self.assertNotIn("据客户提供", prompt)

    def test_placeholder_section_is_not_sent_as_current_draft(self) -> None:
        prompt = _user_prompt(
            {
                "title": "缺口",
                "decision_question": "还值不值得跟",
                "original_context": "客户只给了一句话。",
                "placeholder": True,
                "current_text": "这一节还没写。",
                "materials": [],
                "excerpts": [],
                "claims": [],
            }
        )
        self.assertIn("经理原话：客户只给了一句话。", prompt)
        self.assertIn("这一节还是空的", prompt)
        self.assertNotIn("现稿：\n这一节还没写。", prompt)
        self.assertIn("写缺口稿", _REVISION_PROMPT)
        self.assertIn("不要只在原文外加口径声明", _REVISION_PROMPT)
        # 给经理的是整理稿：论文式小标题、分条换行，不能只写一两句话
        self.assertIn("写成整理稿，不是摘要", _REVISION_PROMPT)
        self.assertIn("版式按论文来", _REVISION_PROMPT)
        self.assertIn("并列事实分条罗列", _REVISION_PROMPT)
        self.assertIn("也禁止整节只有一两句话就收尾", _REVISION_PROMPT)
        self.assertIn("换行必须是真实换行", _REVISION_PROMPT)
        self.assertIn("详略按材料定", _REVISION_PROMPT)
        self.assertIn("用小标题分小节", _REVISION_PROMPT)
        self.assertIn("不要把几条塞进同一段", _REVISION_PROMPT)

    def test_parse_accepts_fenced_json_and_keeps_one_of_each_kind(self) -> None:
        content = """
        先拟如下：
        ```json
        {"proposals": [
          {"kind": "finding", "text": "现有材料只支持缺口判断。"},
          {"kind": "option", "text": "先补租户结构。"},
          {"kind": "finding", "text": "第二条总判断应被丢掉。"},
          {"kind": "source", "text": "不能写成来源。"}
        ]}
        ```
        """
        proposals = parse_draft_proposals(content)
        self.assertEqual(
            proposals,
            [
                {"kind": "finding", "text": "现有材料只支持缺口判断。"},
                {"kind": "option", "text": "先补租户结构。"},
            ],
        )

    def test_parse_revision_keeps_one_revision_and_drops_other_kinds(self) -> None:
        content = """
        {"proposals": [
          {"kind": "revision", "text": "现有材料只支持缺口判断，口径仍待补。"},
          {"kind": "finding", "text": "不能当成改稿。"},
          {"kind": "revision", "text": "第二条改稿应被丢掉。"}
        ]}
        """
        proposals = parse_draft_proposals(content, allowed_kinds={"revision"})
        self.assertEqual(
            proposals,
            [{"kind": "revision", "text": "现有材料只支持缺口判断，口径仍待补。"}],
        )

    def test_parse_rejects_unreadable_payload(self) -> None:
        with self.assertRaises(DraftSuggestionError) as raised:
            parse_draft_proposals("不是 JSON")
        self.assertIn("没有改内部稿", str(raised.exception))

    def test_http_adapter_reads_chat_content_without_rewriting_synthetic(self) -> None:
        captured: dict[str, object] = {}

        def opener(request, timeout=None):
            captured["url"] = request.full_url
            captured["authorization"] = request.get_header("Authorization") or dict(
                request.header_items()
            ).get("Authorization")
            captured["timeout"] = timeout
            body = json.loads(request.data.decode("utf-8"))
            captured["model"] = body["model"]
            captured["user"] = body["messages"][1]["content"]
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "proposals": [
                                            {
                                                "kind": "finding",
                                                "text": "现有材料只支持缺口判断，不能定论。",
                                            }
                                        ]
                                    },
                                    ensure_ascii=False,
                                )
                            }
                        }
                    ]
                }
            )

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repository = SqliteRepository(Path(temp_dir.name) / "jingwei.sqlite3")
        repository.migrate()
        import_sample(repository, SAMPLE_PATH)
        before = build_review_context(repository, "DB-001")
        adapter = HttpJsonDraftAdapter(
            url=DEFAULT_DRAFT_URL,
            api_key="test-key",
            model="demo-model",
            opener=opener,
        )
        result = draft_model_suggestions(repository, "DB-001", adapter=adapter)
        after = build_review_context(repository, "DB-001")
        self.assertEqual(captured["url"], DEFAULT_DRAFT_URL)
        self.assertEqual(captured["authorization"], "Bearer test-key")
        self.assertEqual(captured["model"], "demo-model")
        self.assertIn("已挂依据", captured["user"])
        self.assertNotIn("test-key", captured["user"])
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])
        self.assertEqual(
            [claim["verification_status"] for claim in after["claims"]],
            [claim["verification_status"] for claim in before["claims"]],
        )
        self.assertEqual(len(result["suggestion_ids"]), 1)
        self.assertEqual(after["suggestions"][0]["text"], "现有材料只支持缺口判断，不能定论。")
        self.assertIn("模型先拟", after["suggestions"][0]["limitation"])

    def test_network_failure_does_not_write_suggestion(self) -> None:
        def opener(request, timeout=None):
            raise URLError("refused")

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repository = SqliteRepository(Path(temp_dir.name) / "jingwei.sqlite3")
        repository.migrate()
        import_sample(repository, SAMPLE_PATH)
        before = build_review_context(repository, "DB-001")
        adapter = HttpJsonDraftAdapter(
            url=DEFAULT_DRAFT_URL,
            api_key="test-key",
            model="demo-model",
            opener=opener,
        )
        with self.assertRaises(DraftSuggestionError) as raised:
            draft_model_suggestions(repository, "DB-001", adapter=adapter)
        after = build_review_context(repository, "DB-001")
        self.assertIn("模型没连上", str(raised.exception))
        self.assertNotIn("test-key", str(raised.exception))
        self.assertEqual(after["suggestions"], [])
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])

    def test_default_resolver_uses_env_without_touching_draft(self) -> None:
        def opener(request, timeout=None):
            return FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"proposals":[{"kind":"option","text":"先补租户结构。"}]}'
                            }
                        }
                    ]
                }
            )

        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        repository = SqliteRepository(Path(temp_dir.name) / "jingwei.sqlite3")
        repository.migrate()
        import_sample(repository, SAMPLE_PATH)
        before = build_review_context(repository, "DB-001")
        with patch(
            "app.adapters.http_draft.resolve_draft_adapter",
            return_value=HttpJsonDraftAdapter(
                url=DEFAULT_DRAFT_URL,
                api_key="env-key",
                model="env-model",
                opener=opener,
            ),
        ):
            result = draft_model_suggestions(repository, "DB-001")
        after = build_review_context(repository, "DB-001")
        self.assertEqual(result["review_context"]["suggestions"][0]["kind"], "option")
        self.assertEqual(after["block"]["current_text"], before["block"]["current_text"])


if __name__ == "__main__":
    unittest.main()
