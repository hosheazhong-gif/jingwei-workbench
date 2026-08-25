from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.api import dispatch_post
from app.application.export_folder import (
    EXPORT_LABELS,
    save_export_to_folder,
    safe_folder_name,
)
from app.application.import_sample import import_sample

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ExportFolderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name) / "导出"

    def _export(self, filename: str = "某题.md") -> dict:
        return {
            "filename": filename,
            "content": "正文",
            "content_encoding": "utf-8",
            "exporter_key": "markdown",
        }

    def test_title_becomes_a_safe_folder_name(self) -> None:
        # 题目名里带路径分隔符时不能穿出去，也不能在 Windows 上留结尾的点
        self.assertEqual(safe_folder_name("走查·尽调/蜜雪:冰城", "P-1"), "走查·尽调蜜雪冰城")
        self.assertEqual(safe_folder_name("../../etc", "P-1"), "etc")
        self.assertEqual(safe_folder_name("题目.", "P-1"), "题目")
        self.assertEqual(safe_folder_name("   ", "P-1"), "P-1")
        self.assertEqual(safe_folder_name(None, "P-1"), "P-1")
        self.assertLessEqual(len(safe_folder_name("长" * 300, "P-1")), 80)

    def test_saved_file_says_which_one_it_is(self) -> None:
        path = save_export_to_folder(
            self._export(),
            exports_root=self.root,
            project_name="走查·尽调",
            project_id="P-005",
            stamp="2026-08-23",
            label=EXPORT_LABELS["markdown"],
        )
        self.assertEqual(path.parent.name, "走查·尽调")
        # 文件夹已经是题目名了，文件名写清是哪一份更有用
        self.assertEqual(path.name, "2026-08-23 整理稿.md")
        self.assertEqual(path.read_text(encoding="utf-8"), "正文")

    def test_export_never_overwrites_an_existing_file(self) -> None:
        # 上一版可能已经发出去了，导出不许覆盖它。
        names = []
        for _ in range(3):
            names.append(
                save_export_to_folder(
                    self._export(),
                    exports_root=self.root,
                    project_name="走查·尽调",
                    project_id="P-005",
                    stamp="2026-08-23",
                    label="整理稿",
                ).name
            )
        self.assertEqual(
            names,
            ["2026-08-23 整理稿.md", "2026-08-23 整理稿 (2).md", "2026-08-23 整理稿 (3).md"],
        )

    def test_binary_export_is_written_as_bytes(self) -> None:
        export = {
            "filename": "某题.docx",
            "content": "AAEC",  # base64 of b"\x00\x01\x02"
            "content_encoding": "base64",
            "exporter_key": "word",
        }
        path = save_export_to_folder(
            export,
            exports_root=self.root,
            project_name="某题",
            project_id="P-1",
            stamp="2026-08-23",
            label="整理稿",
        )
        self.assertEqual(path.read_bytes(), b"\x00\x01\x02")


class ExportOverHttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(
            Path(self.temp_dir.name) / "jingwei.sqlite3",
            files_root=Path(self.temp_dir.name) / "files",
        )
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_export_without_the_flag_writes_nothing(self) -> None:
        status, payload = dispatch_post(
            self.repository, f"/projects/{self.project_id}/exports/markdown", {}
        )
        self.assertEqual(status, 200)
        self.assertNotIn("saved_path", payload)
        self.assertTrue(payload["content"])
        # 不传参数就只是导出，不该在磁盘上留任何东西
        self.assertFalse((PROJECT_ROOT / "导出").exists())

    def test_saving_does_not_touch_the_ledger(self) -> None:
        with self.repository.connect() as connection:
            before = {
                row["id"]: row["verification_status"]
                for row in connection.execute(
                    "SELECT id, verification_status FROM claims"
                )
            }
            drafts_before = {
                row["id"]: row["current_text"]
                for row in connection.execute(
                    "SELECT id, current_text FROM deliverable_blocks"
                )
            }
        status, payload = dispatch_post(
            self.repository, f"/projects/{self.project_id}/exports/markdown", {}
        )
        self.assertEqual(status, 200)
        self.assertTrue(payload["confirmation"]["verification_status_unchanged"])
        self.assertTrue(payload["confirmation"]["current_text_unchanged"])
        with self.repository.connect() as connection:
            after = {
                row["id"]: row["verification_status"]
                for row in connection.execute(
                    "SELECT id, verification_status FROM claims"
                )
            }
            drafts_after = {
                row["id"]: row["current_text"]
                for row in connection.execute(
                    "SELECT id, current_text FROM deliverable_blocks"
                )
            }
        self.assertEqual(before, after)
        self.assertEqual(drafts_before, drafts_after)


if __name__ == "__main__":
    unittest.main()
