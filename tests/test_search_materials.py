from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.http_draft import parse_search_queries
from app.adapters.http_search import (
    BraveSearchAdapter,
    DuckDuckGoHtmlSearchAdapter,
    PublicHtmlSearchAdapter,
    SearchChallengeError,
    looks_like_search_challenge,
    parse_brave_payload,
    parse_duckduckgo_html,
    parse_duckduckgo_lite,
    resolve_search_adapter,
    unwrap_result_url,
)
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.candidate_source import discard_web_candidate
from app.application.create_project import create_project
from app.application.import_sample import import_sample
from app.application.search_materials import SearchMaterialsError, search_project_materials
from app.projections.candidates import build_candidate_source_projection
from app.projections.report import build_report_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"

DDG_HTML = """
<html><body>
<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpark">Park page</a>
<a class="result__a" href="https://example.org/policy">Policy note</a>
<a class="result__a" href="//duckduckgo.com/about">About DDG</a>
</body></html>
"""

DDG_LITE_HTML = """
<html><body>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Flite" class='result-link'>Lite page</a>
<a rel="nofollow" href="//duckduckgo.com/about" class='result-link'>About</a>
</body></html>
"""

CHALLENGE_HTML = """
<html><body>
<form id="challenge-form" action="//duckduckgo.com/anomaly.js?sv=html"></form>
</body></html>
"""


class _FakeResponse:
    def __init__(self, raw: bytes) -> None:
        self._raw = raw

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:
        if n < 0:
            return self._raw
        return self._raw[:n]


def _static_opener(body: str):
    raw = body.encode("utf-8")

    def open_request(request: Request, timeout: int = 0) -> _FakeResponse:
        return _FakeResponse(raw)

    return open_request


def _routed_opener(bodies: dict[str, str]):
    def open_request(request: Request, timeout: int = 0) -> _FakeResponse:
        url = getattr(request, "full_url", "") or str(request)
        for needle, body in bodies.items():
            if needle in url:
                return _FakeResponse(body.encode("utf-8"))
        return _FakeResponse(b"<html></html>")

    return open_request


class FakeSearchAdapter:
    key = "fake"

    def __init__(self, hits: list[dict[str, str]] | None = None) -> None:
        self.hits = hits or []
        self.queries: list[str] = []

    def search(self, query: str) -> list[dict[str, str]]:
        self.queries.append(query)
        return list(self.hits)


class FakeQueryAdapter:
    key = "fake_query"

    def __init__(self, queries: list[str]) -> None:
        self.queries = queries
        self.contexts: list[dict] = []

    def propose(self, context: dict) -> list[dict[str, str]]:
        self.contexts.append(dict(context))
        return [{"kind": "query", "text": item} for item in self.queries]


class SearchParserTest(unittest.TestCase):
    def test_duckduckgo_html_unwraps_uddg_and_drops_engine_links(self) -> None:
        hits = parse_duckduckgo_html(DDG_HTML)
        urls = [item["url"] for item in hits]
        self.assertEqual(urls, ["https://example.com/park", "https://example.org/policy"])
        self.assertEqual(hits[0]["title"], "Park page")

    def test_duckduckgo_lite_unwraps_uddg(self) -> None:
        hits = parse_duckduckgo_lite(DDG_LITE_HTML)
        self.assertEqual([item["url"] for item in hits], ["https://example.com/lite"])
        self.assertEqual(hits[0]["title"], "Lite page")

    def test_challenge_page_is_detected(self) -> None:
        self.assertTrue(looks_like_search_challenge(CHALLENGE_HTML))
        self.assertFalse(looks_like_search_challenge(DDG_HTML))

    def test_html_adapter_refuses_challenge_page(self) -> None:
        adapter = DuckDuckGoHtmlSearchAdapter(opener=_static_opener(CHALLENGE_HTML))
        with self.assertRaises(SearchChallengeError) as raised:
            adapter.search("远川园区冷链")
        self.assertIn("公开搜索被拦截", str(raised.exception))

    def test_public_adapter_falls_back_to_html(self) -> None:
        adapter = PublicHtmlSearchAdapter(
            opener=_routed_opener(
                {
                    "lite.duckduckgo.com": CHALLENGE_HTML,
                    "html.duckduckgo.com": DDG_HTML,
                }
            )
        )
        hits = adapter.search("远川园区冷链")
        self.assertEqual(
            [item["url"] for item in hits],
            ["https://example.com/park", "https://example.org/policy"],
        )

    def test_public_adapter_retries_lite_after_challenge(self) -> None:
        calls = {"lite": 0}

        def opener(request: Request, timeout: int = 0) -> _FakeResponse:
            url = getattr(request, "full_url", "") or str(request)
            if "lite.duckduckgo.com" in url:
                calls["lite"] += 1
                if calls["lite"] == 1:
                    return _FakeResponse(CHALLENGE_HTML.encode("utf-8"))
                return _FakeResponse(DDG_LITE_HTML.encode("utf-8"))
            if "html.duckduckgo.com" in url:
                raise AssertionError("html should wait for lite retry")
            return _FakeResponse(b"<html></html>")

        hits = PublicHtmlSearchAdapter(opener=opener).search("远川园区冷链")
        self.assertEqual([item["url"] for item in hits], ["https://example.com/lite"])
        self.assertEqual(calls["lite"], 2)

    def test_public_adapter_refuses_when_both_challenged(self) -> None:
        adapter = PublicHtmlSearchAdapter(opener=_static_opener(CHALLENGE_HTML))
        with self.assertRaises(SearchChallengeError) as raised:
            adapter.search("远川园区冷链")
        self.assertIn("JINGWEI_SEARCH_API_KEY", str(raised.exception))

    def test_unwrap_rejects_search_engine_hosts(self) -> None:
        self.assertIsNone(unwrap_result_url("https://duckduckgo.com/?q=x"))
        self.assertEqual(
            unwrap_result_url("https://example.com/a#frag"),
            "https://example.com/a",
        )

    def test_brave_payload_reads_web_results(self) -> None:
        hits = parse_brave_payload(
            {
                "web": {
                    "results": [
                        {
                            "title": "A",
                            "url": "https://example.com/a",
                            "description": "note",
                        },
                        {"title": "B", "url": "https://duckduckgo.com/x"},
                    ]
                }
            }
        )
        self.assertEqual(hits, [{"url": "https://example.com/a", "title": "A", "snippet": "note"}])

    def test_resolve_default_is_public_html(self) -> None:
        adapter = resolve_search_adapter(environ={})
        self.assertIsInstance(adapter, PublicHtmlSearchAdapter)

    def test_brave_without_key_refuses(self) -> None:
        with self.assertRaises(Exception) as raised:
            resolve_search_adapter(environ={"JINGWEI_SEARCH_PROVIDER": "brave"})
        self.assertIn("JINGWEI_SEARCH_API_KEY", str(raised.exception))

    def test_brave_with_key_uses_brave_adapter(self) -> None:
        adapter = resolve_search_adapter(
            environ={
                "JINGWEI_SEARCH_PROVIDER": "brave",
                "JINGWEI_SEARCH_API_KEY": "test-token",
            }
        )
        self.assertIsInstance(adapter, BraveSearchAdapter)


class SearchMaterialsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_search_writes_candidates_not_sources_or_draft(self) -> None:
        before_text = _block_text(self.repository, "DB-001")
        before_status = _claim_status(self.repository, "C-002")
        before_sources = self.repository.list_source_ids(self.project_id)
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/found", "title": "Found page", "snippet": "occupancy"}]
        )
        result = search_project_materials(
            self.repository,
            self.project_id,
            question_id="RQ-01",
            search_adapter=searcher,
        )
        self.assertGreaterEqual(len(searcher.queries), 1)
        self.assertEqual(result["added_count"], 1)
        self.assertEqual(result["added"][0]["status"], "captured")
        self.assertFalse(result["added"][0]["can_promote"])
        self.assertEqual(result["confirmation"]["record_kind"], "search_candidates")
        self.assertFalse(result["confirmation"]["source_created"])
        self.assertFalse(result["confirmation"]["deliverable_changed"])
        listing = build_candidate_source_projection(self.repository, self.project_id)
        self.assertEqual(listing["candidates"][0]["url"], "https://example.com/found")
        self.assertEqual(self.repository.list_source_ids(self.project_id), before_sources)
        self.assertEqual(_block_text(self.repository, "DB-001"), before_text)
        self.assertEqual(_claim_status(self.repository, "C-002"), before_status)
        self.assertEqual(
            build_report_projection(self.repository, self.project_id)["blocks"][0]["current_text"],
            before_text,
        )

    def test_model_cannot_invent_candidate_urls(self) -> None:
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/real-hit", "title": "Real"}]
        )
        drafter = FakeQueryAdapter(
            ["https://evil.example/invented", "cold storage occupancy"]
        )
        result = search_project_materials(
            self.repository,
            self.project_id,
            question_id="RQ-01",
            search_adapter=searcher,
            draft_adapter=drafter,
        )
        urls = [item["url"] for item in result["added"]]
        self.assertEqual(urls, ["https://example.com/real-hit"])
        self.assertNotIn("https://evil.example/invented", urls)
        self.assertEqual(drafter.contexts[0]["task"], "search_queries")
        self.assertIn("cold storage occupancy", searcher.queries)

    def test_duplicate_hits_are_skipped(self) -> None:
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/found", "title": "Found"}]
        )
        search_project_materials(
            self.repository,
            self.project_id,
            question_id="RQ-01",
            search_adapter=searcher,
        )
        again = search_project_materials(
            self.repository,
            self.project_id,
            question_id="RQ-01",
            search_adapter=searcher,
        )
        self.assertEqual(again["added_count"], 0)
        self.assertGreaterEqual(again["skipped_count"], 1)
        listing = build_candidate_source_projection(self.repository, self.project_id)
        self.assertEqual(len(listing["candidates"]), 1)

    def test_discarded_url_is_not_recaptured_as_a_new_duplicate(self) -> None:
        # 现场缺陷（docs/20 §6，2026-08-22 未修条）：一条链接被「这轮不用」之后，
        # 再搜一次会把同一网址当成没见过，重新收进一条新候选；两条各自独立地
        # 被排除后，「这轮不用的」抽屉里就会看到两条标题相同的行，像哪条被换掉了。
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/found", "title": "Found"}]
        )
        first = search_project_materials(
            self.repository,
            self.project_id,
            question_id="RQ-01",
            search_adapter=searcher,
        )
        discard_web_candidate(self.repository, first["added"][0]["id"])
        again = search_project_materials(
            self.repository,
            self.project_id,
            question_id="RQ-01",
            search_adapter=searcher,
        )
        self.assertEqual(again["added_count"], 0)
        self.assertGreaterEqual(again["skipped_count"], 1)
        listing = build_candidate_source_projection(self.repository, self.project_id)
        matching = [
            item for item in listing["candidates"] if item["url"] == "https://example.com/found"
        ]
        self.assertEqual(len(matching), 1)

    def test_blank_project_can_search_from_brief(self) -> None:
        created = create_project(
            self.repository,
            name="空白搜题",
            original_context="领导要看园区还值不值得跟。",
        )
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/blank", "title": "Blank hit"}]
        )
        result = search_project_materials(
            self.repository,
            created["project_id"],
            search_adapter=searcher,
        )
        self.assertEqual(result["added_count"], 1)
        self.assertTrue(searcher.queries)

    def test_stops_after_first_query_that_adds(self) -> None:
        created = create_project(
            self.repository,
            name="一次一搜",
            original_context="领导要看园区还值不值得跟。",
            questions=["occupancy", "tenant mix"],
        )
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/first", "title": "First"}]
        )
        question_id = created["brief_projection"]["questions"][0]["id"]
        result = search_project_materials(
            self.repository,
            created["project_id"],
            question_id=question_id,
            search_adapter=searcher,
        )
        self.assertEqual(searcher.queries, ["occupancy"])
        self.assertEqual(result["added_count"], 1)

    def test_search_challenge_does_not_write_candidates(self) -> None:
        class ChallengedSearch:
            key = "challenged"

            def search(self, query: str) -> list[dict[str, str]]:
                raise SearchChallengeError("公开搜索被拦截。没有写入候选，也没有改稿。")

        with self.assertRaises(SearchMaterialsError) as raised:
            search_project_materials(
                self.repository,
                self.project_id,
                question_id="RQ-01",
                search_adapter=ChallengedSearch(),
            )
        self.assertIn("公开搜索被拦截", str(raised.exception))
        self.assertIn("检索：", str(raised.exception))
        self.assertEqual(
            build_candidate_source_projection(self.repository, self.project_id)["candidates"],
            [],
        )

    def test_search_without_question_refuses_when_questions_exist(self) -> None:
        searcher = FakeSearchAdapter([{"url": "https://example.com/x", "title": "X"}])
        with self.assertRaises(SearchMaterialsError) as raised:
            search_project_materials(
                self.repository,
                self.project_id,
                search_adapter=searcher,
            )
        self.assertIn("先点开左边要搜的那条问题", str(raised.exception))
        self.assertEqual(
            build_candidate_source_projection(self.repository, self.project_id)["candidates"],
            [],
        )

    def test_missing_question_refuses(self) -> None:
        searcher = FakeSearchAdapter([{"url": "https://example.com/x", "title": "X"}])
        with self.assertRaises(SearchMaterialsError) as raised:
            search_project_materials(
                self.repository,
                self.project_id,
                question_id="RQ-NOT-THERE",
                search_adapter=searcher,
            )
        self.assertIn("没有这条本轮问题", str(raised.exception))
        self.assertEqual(
            build_candidate_source_projection(self.repository, self.project_id)["candidates"],
            [],
        )

    def test_parse_search_queries_drops_urls(self) -> None:
        queries = parse_search_queries(
            '{"queries":["cold chain park","https://evil.example/x","occupancy"]}'
        )
        self.assertEqual(queries, ["cold chain park", "occupancy"])


class SearchMaterialsHttpTest(unittest.TestCase):
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

    def test_http_search_uses_injected_adapter(self) -> None:
        searcher = FakeSearchAdapter(
            [{"url": "https://example.com/http-search", "title": "HTTP hit"}]
        )
        with patch(
            "app.adapters.http_search.resolve_search_adapter",
            return_value=searcher,
        ):
            status, payload = _http_json(
                "POST",
                self.server.origin + f"/projects/{self.project_id}/material-search",
                {"question_id": "RQ-01"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["added_count"], 1)
        self.assertEqual(payload["added"][0]["url"], "https://example.com/http-search")
        self.assertTrue(payload["added"][0]["id"].startswith("CS-"))
        self.assertEqual(_claim_status(self.repository, "C-002"), "captured")


def _block_text(repository: SqliteRepository, block_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT current_text FROM deliverable_blocks WHERE id = ?",
            (block_id,),
        ).fetchone()["current_text"]


def _claim_status(repository: SqliteRepository, claim_id: str) -> str:
    with repository.connect() as connection:
        return connection.execute(
            "SELECT verification_status FROM claims WHERE id = ?",
            (claim_id,),
        ).fetchone()["verification_status"]


def _http_json(method: str, url: str, payload: dict | None = None) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method)
    if payload is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urlopen(request) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        payload = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, payload


if __name__ == "__main__":
    unittest.main()
