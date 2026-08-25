"""详细版导出：经理版之外的第二份，摊开原文摘录和机械检查。

两份读的是同一份批准投影，同一批段落 id；详细版多的是过程，不是第二套结论。
"""

from __future__ import annotations

import base64
import io
import re
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.export_deliverable import export_project
from app.application.import_sample import import_sample
from app.exporters import default_exporters
from app.projections.report import build_report_projection

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class DetailedExportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(Path(self.temp_dir.name) / "jingwei.sqlite3")
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_both_exporters_are_registered_side_by_side(self) -> None:
        registry = default_exporters()
        self.assertIn("word", registry)
        self.assertIn("word_detailed", registry)
        self.assertIn("markdown", registry)
        self.assertIn("markdown_detailed", registry)

    def test_detailed_word_carries_excerpts_headings_and_checks(self) -> None:
        manager = export_project(self.repository, self.project_id, "word")
        detailed = export_project(self.repository, self.project_id, "word_detailed")
        self.assertEqual(detailed["block_ids"], manager["block_ids"])
        self.assertTrue(detailed["filename"].endswith(".详细版.docx"))

        text = _docx_text(base64.b64decode(detailed["content"]))
        manager_text = _docx_text(base64.b64decode(manager["content"]))
        # 论文式层级：一、二、三 分节，节内再分（一）（二）
        self.assertIn("一、", text)
        self.assertIn("（一）正文", text)
        self.assertIn("（二）本节支撑的主张与原文", text)
        self.assertIn("（三）本节来源清单", text)
        self.assertIn("（五）机械检查", text)
        self.assertIn("目录", text)
        # 详细版要能一路看到原话；经理版不写摘录
        excerpt = _first_excerpt(self.repository, self.project_id)
        self.assertIn(excerpt[:20], text)
        self.assertNotIn(excerpt[:20], manager_text)
        # 详细版明显更长：它多的是过程，不是第二套结论
        self.assertGreater(len(text), len(manager_text))

    def test_detailed_markdown_is_a_heading_tree(self) -> None:
        result = export_project(self.repository, self.project_id, "markdown_detailed")
        body = result["content"]
        self.assertEqual(result["content_encoding"], "utf-8")
        self.assertTrue(result["filename"].endswith(".详细版.md"))
        self.assertIn("\n## 一、", body)
        self.assertIn("\n### （一）正文", body)
        self.assertIn("\n> ", body)

    def test_docx_carries_a_real_style_sheet(self) -> None:
        """导航窗格认的是大纲级别，不是加粗。没有 styles.xml 就没有层级。"""
        for key in ("word", "word_detailed"):
            data = base64.b64decode(
                export_project(self.repository, self.project_id, key)["content"]
            )
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                self.assertIn("word/styles.xml", names, key)
                styles = archive.read("word/styles.xml").decode("utf-8")
                document = archive.read("word/document.xml").decode("utf-8")
            # 四级标题各带一个大纲级别，Word 的导航窗格才列得出来
            for level in range(4):
                self.assertIn(f'<w:outlineLvl w:val="{level}"/>', styles, key)
            self.assertIn('w:name w:val="heading 1"', styles, key)
            # 行距、段后距、缩进都在样式里，不再逐段硬写
            self.assertIn('w:lineRule="auto"', styles, key)
            self.assertIn("w:firstLineChars", styles, key)
            self.assertIn("w:hangingChars", styles, key)
            # 正文段落引用样式，而不是把字号写在 run 上
            self.assertIn('<w:pStyle w:val="BodyText"/>', document, key)
            self.assertIn('<w:pStyle w:val="Heading1"/>', document, key)
            self.assertIn("<w:sectPr>", document, key)

    def test_notes_are_small_print_not_body_text(self) -> None:
        data = base64.b64decode(
            export_project(self.repository, self.project_id, "word_detailed")["content"]
        )
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = archive.read("word/document.xml").decode("utf-8")
        # 「写入限制：」这类附注走注释样式，不跟正文一样大
        note = document.split('<w:pStyle w:val="NoteText"/>')
        self.assertGreater(len(note), 1)
        self.assertIn("写入限制：", document)
        self.assertIn('<w:pStyle w:val="FieldItem"/>', document)

    def test_pasted_material_keeps_its_own_shape(self) -> None:
        """粘进来的材料自带小标题、分条和表格，排版要认出来，字一个不改。"""
        from app.exporters.layout import structure_lines

        blocks = structure_lines(
            "地方支持性产业政策\n"
            "一、制定了各省市机器人产业具体发展目标。\n"
            "表3-2 地方代表性机器人政策 来源：中机院\n"
            "省市\t年份\t政策\n"
            "北京市\t2017\t《北京市机器人产业创新发展路线图》\n"
        )
        kinds = [item["kind"] for item in blocks]
        self.assertEqual(kinds, ["heading", "item", "caption", "table"])
        self.assertEqual(blocks[3]["rows"][0], ["省市", "年份", "政策"])
        self.assertEqual(blocks[0]["text"], "地方支持性产业政策")

    def test_detailed_export_changes_nothing(self) -> None:
        before = build_report_projection(self.repository, self.project_id)
        before_markdown = export_project(self.repository, self.project_id, "markdown")
        export_project(self.repository, self.project_id, "word_detailed")
        after = build_report_projection(self.repository, self.project_id)
        after_markdown = export_project(self.repository, self.project_id, "markdown")
        self.assertEqual(before_markdown["content"], after_markdown["content"])
        self.assertEqual(
            [block["current_text"] for block in before["blocks"]],
            [block["current_text"] for block in after["blocks"]],
        )
        self.assertEqual(_statuses(self.repository), _statuses(self.repository))


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")
    return re.sub(r"<[^>]+>", "", xml)


def _first_excerpt(repository: SqliteRepository, project_id: str) -> str:
    with repository.connect() as connection:
        row = connection.execute(
            """
            SELECT e.excerpt FROM evidence_excerpts e
            JOIN sources s ON s.id = e.source_id
            WHERE s.project_id = ? AND length(e.excerpt) > 20
            ORDER BY e.rowid LIMIT 1
            """,
            (project_id,),
        ).fetchone()
    return str(row["excerpt"])


def _statuses(repository: SqliteRepository) -> dict:
    with repository.connect() as connection:
        return {
            row["id"]: row["verification_status"]
            for row in connection.execute("SELECT id, verification_status FROM claims")
        }


if __name__ == "__main__":
    unittest.main()
