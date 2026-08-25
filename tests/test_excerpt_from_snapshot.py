from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

from app.adapters.local_source import sha256_file
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.application.candidate_source import (
    capture_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.create_project import create_project
from app.application.excerpt_from_snapshot import (
    ExcerptFromSnapshotError,
    adopt_snapshot_excerpts,
    draft_snapshot_excerpts,
    snapshot_plain_text,
)
from app.application.material_question import assign_material_question
from app.application.source_snapshot import build_snapshot_view, read_source_snapshot
from app.projections.workbench import build_workbench_projection
from tests.test_draft_suggestion import MemoryWebAdapter


class RichWebAdapter:
    key = "web_page"

    def snapshot(self, url: str, project_files: Path) -> dict:
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / "snapshot.bin"
        destination.write_text(
            "<html><head><title>Arm</title><script>ignore()</script></head>"
            "<body><p>减速器占成本两成以上。</p>"
            '<p><a href="https://example.com/joint">关节模组由三家供应</a></p>'
            "</body></html>",
            encoding="utf-8",
        )
        return {
            "file_name": "snapshot.bin",
            "original_url": url,
            "snapshot_path": destination,
            "content_hash": sha256_file(destination),
            "availability": "available",
        }


class ScriptedExcerptAdapter:
    key = "excerpts"

    def __init__(self, excerpts: list[str]) -> None:
        self.excerpts = excerpts
        self.context: dict | None = None

    def propose(self, context):
        self.context = context
        return [{"kind": "excerpt", "text": item} for item in self.excerpts]


class ExcerptFromSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(
            self.database_path, files_root=Path(self.temp_dir.name) / "files"
        )
        self.repository.migrate()
        created = create_project(
            self.repository,
            name="手臂图谱",
            original_context="打通机器人手臂的产业链条。",
            questions=["零件和上游清不清楚"],
        )
        self.project_id = created["project_id"]
        self.question_id = created["brief_projection"]["questions"][0]["id"]
        self.block_id = created["report"]["blocks"][0]["id"]
        self.before_text = created["report"]["blocks"][0]["current_text"]
        captured = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/arm",
            title="Arm page",
            question_id=self.question_id,
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=RichWebAdapter(),
        )
        self.source_id = promoted["source_id"]

    def test_plain_text_skips_script_and_keeps_body(self) -> None:
        body, content_type = read_source_snapshot(self.repository, self.source_id)
        text = snapshot_plain_text(body, content_type)
        self.assertIn("减速器占成本两成以上", text)
        self.assertIn("关节模组由三家供应", text)
        self.assertNotIn("ignore()", text)

    def test_readable_snapshot_page_shows_extracted_text(self) -> None:
        page, content_type = build_snapshot_view(self.repository, self.source_id)
        self.assertIn("text/html", content_type)
        html = page.decode("utf-8")
        self.assertIn("保存的网页快照", html)
        self.assertIn("减速器占成本两成以上", html)
        self.assertIn("原链接", html)
        self.assertIn('href="https://example.com/arm"', html)
        self.assertIn('href="https://example.com/joint"', html)
        self.assertNotIn("ignore()", html)

    def test_snapshot_page_puts_body_before_in_page_links(self) -> None:
        # 快照页必须先显示正文，再列出页内链接，避免导航链接占满第一屏。
        page, _ = build_snapshot_view(self.repository, self.source_id)
        html = page.decode("utf-8")
        body_index = html.index("减速器占成本两成以上")
        links_heading_index = html.index("页里的链接")
        self.assertLess(
            body_index,
            links_heading_index,
            "正文应该排在「页里的链接」列表前面，不能让人先滚过链接才看到内容",
        )

    def test_pdf_snapshot_is_not_mistaken_for_html(self) -> None:
        # 网页快照一律落盘成 snapshot.bin，
        # `_content_type` 看后缀认不出来，又因为 kind == "web_page" 判成 HTML；
        # 于是 PDF 的二进制被当正文抽出一堆垃圾，长度过了门槛，两个键照给，
        # 点下去必然是死路。文件头必须先说话。
        from app.application.source_snapshot import _content_type

        pdf_bytes = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"stream\n\xde\xad\xbe\xef" * 400
        path = Path(self.temp_dir.name) / "snapshot.bin"
        path.write_bytes(pdf_bytes)
        self.assertEqual(
            _content_type(path, "web_page", pdf_bytes), "application/pdf"
        )
        # 认成 PDF 之后就不该再抽出任何"正文"。
        self.assertEqual(snapshot_plain_text(pdf_bytes, "application/pdf"), "")
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
        path.write_bytes(png_bytes)
        self.assertEqual(_content_type(path, "web_page", png_bytes), "image/png")
        # HTML 仍然认得出来，没有被文件头判断误伤。
        html_bytes = b"<html><body><p>\xe6\xad\xa3\xe6\x96\x87</p></body></html>"
        path.write_bytes(html_bytes)
        self.assertIn("text/html", _content_type(path, "web_page", html_bytes))

    def test_every_snapshot_state_explains_itself(self) -> None:
        # 缺陷的另一半：抽不出正文时两个键都不给，但 snapshot_note 是 None，
        # 卡片上既没有按钮也没有一句话，人不知道为什么。每种情况都要有话。
        from app.application.source_snapshot import snapshot_capabilities

        project_files = self.repository.files_root / self.project_id
        project_files.mkdir(parents=True, exist_ok=True)

        pdf_path = project_files / "doc.bin"
        pdf_path.write_bytes(b"%PDF-1.7\n" + b"\xde\xad\xbe\xef" * 200)
        pdf = snapshot_capabilities(
            self.repository,
            {"kind": "web_page", "snapshot_path": f"{self.project_id}/doc.bin"},
        )
        self.assertTrue(pdf["can_view_snapshot"])
        self.assertFalse(pdf["can_scrape_snapshot"])
        self.assertIn("PDF", pdf["snapshot_note"])
        self.assertIn("手工粘", pdf["snapshot_note"])

        missing = snapshot_capabilities(
            self.repository,
            {"kind": "web_page", "snapshot_path": f"{self.project_id}/nope.bin"},
        )
        self.assertFalse(missing["can_view_snapshot"])
        self.assertTrue(missing["snapshot_note"])

        none_at_all = snapshot_capabilities(
            self.repository, {"kind": "web_page", "snapshot_path": ""}
        )
        self.assertFalse(none_at_all["can_view_snapshot"])
        self.assertTrue(none_at_all["snapshot_note"])

    def test_model_quotes_must_be_verbatim(self) -> None:
        adapter = ScriptedExcerptAdapter(
            ["减速器占成本两成以上", "关节模组由三家供应", "模型发明的产量数字"]
        )
        drafted = draft_snapshot_excerpts(
            self.repository,
            self.source_id,
            question_id=self.question_id,
            deliverable_block_id=self.block_id,
            adapter=adapter,
        )
        self.assertEqual(
            drafted["excerpts"],
            ["减速器占成本两成以上", "关节模组由三家供应"],
        )
        self.assertEqual(adapter.context["task"], "snapshot_excerpts")
        self.assertEqual(adapter.context["focus_question"], "零件和上游清不清楚")
        workbench = build_workbench_projection(self.repository, self.project_id)
        self.assertEqual(workbench["blocks"][0]["current_text"], self.before_text)
        self.assertEqual(workbench["blocks"][0]["claim_sources"], [])

    def test_invented_only_quotes_are_refused(self) -> None:
        with self.assertRaises(ExcerptFromSnapshotError) as raised:
            draft_snapshot_excerpts(
                self.repository,
                self.source_id,
                question_id=self.question_id,
                adapter=ScriptedExcerptAdapter(["完全编造的句子一二三"]),
            )
        self.assertIn("没有记下原话", str(raised.exception))
        self.assertEqual(
            build_workbench_projection(self.repository, self.project_id)["blocks"][0][
                "current_text"
            ],
            self.before_text,
        )

    def test_adopt_hangs_excerpts_without_rewriting_draft(self) -> None:
        drafted = draft_snapshot_excerpts(
            self.repository,
            self.source_id,
            question_id=self.question_id,
            deliverable_block_id=self.block_id,
            adapter=ScriptedExcerptAdapter(["减速器占成本两成以上"]),
        )
        adopted = adopt_snapshot_excerpts(
            self.repository,
            self.source_id,
            deliverable_block_id=self.block_id,
            excerpts=drafted["excerpts"],
        )
        workbench = adopted["workbench"]
        self.assertEqual(workbench["blocks"][0]["current_text"], self.before_text)
        excerpts = [
            item["excerpt"] for item in workbench["blocks"][0]["claim_sources"]
        ]
        self.assertEqual(excerpts, ["减速器占成本两成以上"])
        self.assertTrue(adopted["confirmation"]["current_text_unchanged"])
        self.assertIn("按材料再写一版", adopted["confirmation"]["message"])

    def test_assign_question_does_not_rewrite_draft(self) -> None:
        captured = capture_web_candidate(
            self.repository,
            self.project_id,
            url="https://example.com/other",
            title="Untagged",
        )
        open_web_candidate(self.repository, captured["candidate_id"])
        promoted = promote_web_candidate(
            self.repository,
            captured["candidate_id"],
            adapter=MemoryWebAdapter(),
        )
        assigned = assign_material_question(
            self.repository,
            source_id=promoted["source_id"],
            question_id=self.question_id,
        )
        self.assertEqual(assigned["research_question_id"], self.question_id)
        self.assertEqual(
            assigned["workbench"]["blocks"][0]["current_text"],
            self.before_text,
        )
        source = next(
            item
            for item in assigned["workbench"]["materials"]["sources"]
            if item["id"] == promoted["source_id"]
        )
        self.assertEqual(source["question_label"], "零件和上游清不清楚")

    def test_http_snapshot_and_excerpt_draft(self) -> None:
        server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        server.start()
        self.addCleanup(server.stop)
        with urlopen(server.origin + f"/sources/{self.source_id}/snapshot") as response:
            self.assertEqual(response.status, 200)
            self.assertIn("text/html", response.headers.get("Content-Type") or "")
            body = response.read().decode("utf-8")
        self.assertIn("减速器占成本两成以上", body)
        self.assertIn("保存的网页快照", body)


if __name__ == "__main__":
    unittest.main()
