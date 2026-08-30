from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer, dispatch_get
from app.templates.registry import VERIFICATION_LEVELS
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection, build_review_context


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ReadOnlyHttpApiTest(unittest.TestCase):
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

    def test_report_api_matches_projection_and_shares_block_ids(self) -> None:
        status, payload = self._get(f"/projects/{self.project_id}/report")
        projection = build_report_projection(self.repository, self.project_id)
        self.assertEqual(status, 200)
        self.assertEqual(payload, projection)
        self.assertEqual(
            {block["id"] for block in payload["blocks"]},
            {"DB-001", "DB-002", "DB-003", "DB-004"},
        )

    def test_review_context_api_matches_projection_and_same_object_ids(self) -> None:
        report_status, report = self._get(f"/projects/{self.project_id}/report")
        review_status, review = self._get("/deliverable-blocks/DB-001/review-context")
        projection = build_review_context(self.repository, "DB-001")
        self.assertEqual(report_status, 200)
        self.assertEqual(review_status, 200)
        self.assertEqual(review, projection)
        block_ids = {block["id"] for block in report["blocks"]}
        self.assertIn(review["block"]["id"], block_ids)
        claim = next(item for item in review["claims"] if item["id"] == "C-002")
        self.assertEqual(claim["evidence"][0]["excerpt"], "60%+ 食品产业客群")
        self.assertIn("据客户提供", claim["delivery_rule"])

    def test_templates_api_lists_only_selectable_templates(self) -> None:
        # 新建题目要能选模板，前端就得有地方问「有哪些模板」。
        status, payload = self._get("/templates")
        self.assertEqual(status, 200)
        keys = [item["key"] for item in payload["templates"]]
        self.assertIn("industry_chain_analysis_presales", keys)
        self.assertIn("commercial_dd_desk", keys)
        # 接缝演练用的假模板不摆到人面前。
        self.assertNotIn("demo_market_scan", keys)
        # 默认模板排第一并标出来，否则界面会默认选中排序第一个。
        self.assertEqual(keys[0], payload["default_key"])
        # 这个接口不读任何项目数据，也不暴露内部字段。介绍页要的三个字段
        # （intro / steps / example）是纯文案，换掉值模型输出不变（PRD 20.10）。
        for item in payload["templates"]:
            self.assertEqual(
                set(item),
                {
                    "key",
                    "name",
                    "brief_prompt",
                    "question_hint_count",
                    "is_default",
                    "intro",
                    "when_to_use",
                    "when_not_to_use",
                    "flow",
                    "steps",
                    "example",
                    "pitfalls",
                    "question_labels",
                    "verification",
                    "loop_walked",
                    "status_label",
                    "status_note",
                    "questions",
                    "sample_briefs",
                },
            )
            # 走查状态和问法来源：没走查过的不许在页面上装成走查过的，
            # 七条问法的来处条数必须跟问法条数对得上（PRD 20.12）。
            self.assertIn(item["verification"], VERIFICATION_LEVELS)
            self.assertEqual(item["status_label"], VERIFICATION_LEVELS[item["verification"]])
            self.assertEqual(item["loop_walked"], item["verification"] == "loop_walked")
            self.assertTrue(item["status_note"], item["key"])
            self.assertEqual(len(item["questions"]), len(item["question_labels"]))
            self.assertEqual(
                [row["label"] for row in item["questions"]], item["question_labels"]
            )
            for row in item["questions"]:
                self.assertTrue(row["source"], item["key"])
                self.assertNotEqual(row["source"], "没注明来处", item["key"])
            # 每个模板都要真的写了介绍，不许空着摆在介绍页上
            self.assertTrue(item["intro"], item["key"])
            self.assertTrue(item["steps"], item["key"])
            self.assertTrue(item["flow"], item["key"])
            self.assertTrue(item["example"]["brief"], item["key"])
            self.assertEqual(len(item["question_labels"]), item["question_hint_count"])
            # 样例必须自己声明不是材料，免得人当证据用
            self.assertIn("不是材料", item["example"]["note"])

    def test_creating_a_project_over_http_honours_the_chosen_template(self) -> None:
        status, payload = self._request(
            "POST",
            "/projects",
            {
                "name": "某标的的案头尽调",
                "original_context": "经理说：先看看这家标的公开材料能撑到哪。",
                "template_key": "commercial_dd_desk",
            },
        )
        self.assertEqual(status, 201)
        listing_status, listing = self._get("/projects")
        self.assertEqual(listing_status, 200)
        created = next(
            item for item in listing["projects"] if item["id"] == payload["project_id"]
        )
        self.assertEqual(created["template_key"], "commercial_dd_desk")
        # 建题目不得动远川园区样本。
        synthetic = next(
            item for item in listing["projects"] if item["id"] == "P-DEMO-001"
        )
        self.assertEqual(synthetic["template_key"], "industry_chain_analysis_presales")

    def test_project_list_says_which_template_each_one_uses(self) -> None:
        # 建完题目之后模板就不再露面，回头只能靠题目名猜（2026-08-23 流水账）。
        status, payload = self._get("/projects")
        self.assertEqual(status, 200)
        for item in payload["projects"]:
            self.assertTrue(item["template_name"], item["id"])
        synthetic = next(x for x in payload["projects"] if x["id"] == "P-DEMO-001")
        self.assertEqual(synthetic["template_name"], "产业链分析（低信息售前研究）")
        bench_status, bench = self._get(f"/projects/{self.project_id}/workbench")
        self.assertEqual(bench_status, 200)
        self.assertEqual(
            bench["project"]["template_name"], "产业链分析（低信息售前研究）"
        )

    def test_dispatch_uses_the_same_functions_as_http(self) -> None:
        status, payload = dispatch_get(
            self.repository, f"/projects/{self.project_id}/report"
        )
        http_status, http_payload = self._get(f"/projects/{self.project_id}/report")
        self.assertEqual((status, payload), (http_status, http_payload))

    def test_missing_project_and_block_return_404(self) -> None:
        project_status, project_payload = self._get("/projects/missing/report")
        block_status, block_payload = self._get(
            "/deliverable-blocks/missing/review-context"
        )
        self.assertEqual(project_status, 404)
        self.assertEqual(block_status, 404)
        self.assertIn("error", project_payload)
        self.assertIn("error", block_payload)

    def test_unknown_and_write_routes_are_rejected(self) -> None:
        unknown_status, unknown_payload = self._get("/projects/P-DEMO-001/missing-route")
        write_status, write_payload = self._request(
            "POST", f"/projects/{self.project_id}/report"
        )
        self.assertEqual(unknown_status, 404)
        self.assertEqual(write_status, 405)
        self.assertIn("error", unknown_payload)
        self.assertIn("只读", write_payload["error"])

    def test_database_file_can_be_deleted_after_http_server_stops(self) -> None:
        extra_path = Path(self.temp_dir.name) / "http-release.sqlite3"
        repository = SqliteRepository(extra_path)
        repository.migrate()
        import_sample(repository, SAMPLE_PATH)
        server = ReadOnlyHttpServer(repository, host="127.0.0.1", port=0)
        server.start()
        try:
            status, payload = _http_json("GET", f"{server.origin}/projects/P-DEMO-001/report")
            self.assertEqual(status, 200)
            self.assertTrue(payload["blocks"][0]["current_text"].strip())
        finally:
            server.stop()
        extra_path.unlink()
        self.assertFalse(extra_path.exists())

    def test_http_server_does_not_share_a_busy_port(self) -> None:
        self.assertFalse(self.server._httpd.allow_reuse_address)

    def test_http_server_rejects_non_loopback_bind(self) -> None:
        with self.assertRaisesRegex(ValueError, "本机工作台"):
            ReadOnlyHttpServer(self.repository, host="0.0.0.0", port=0)

    def test_cross_site_browser_writes_are_rejected(self) -> None:
        before_status, before = self._get("/projects")
        self.assertEqual(before_status, 200)
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "不该写入", "original_context": "跨站请求"},
            headers={
                "Origin": "https://malicious.example",
                "Sec-Fetch-Site": "cross-site",
            },
        )
        self.assertEqual(status, 403)
        self.assertIn("本机工作台", payload["error"])
        after_status, after = self._get("/projects")
        self.assertEqual(after_status, 200)
        self.assertEqual(after, before)

    def test_same_origin_browser_write_still_works(self) -> None:
        status, payload = _http_json(
            "POST",
            self.server.origin + "/projects",
            {"name": "同源浏览器写入", "original_context": "本机工作台"},
            headers={
                "Origin": self.server.origin,
                "Sec-Fetch-Site": "same-origin",
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(payload["project_id"])

    def _get(self, path: str) -> tuple[int, dict]:
        return _http_json("GET", self.server.origin + path)

    def _request(
        self, method: str, path: str, body: dict | None = None
    ) -> tuple[int, dict]:
        return _http_json(method, self.server.origin + path, body)


def _http_json(
    method: str,
    url: str,
    body: dict | None = None,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict]:
    data = None
    request_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(url, method=method, data=data, headers=request_headers)
    try:
        with urlopen(request) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return response.status, payload
    except HTTPError as error:
        payload = json.loads(error.read().decode("utf-8"))
        error.close()
        return error.code, payload


if __name__ == "__main__":
    unittest.main()
