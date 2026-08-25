from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.desktop import prepare_runtime, run_smoke_test, runtime_paths


class DesktopRuntimeTest(unittest.TestCase):
    def test_data_directory_override_controls_database_and_exports(self) -> None:
        root = Path("D:/portable-jingwei-data")
        paths = runtime_paths({"JINGWEI_DATA_DIR": str(root)})

        self.assertEqual(paths.data_dir, root)
        self.assertEqual(paths.database, root / "jingwei.sqlite3")
        self.assertEqual(paths.exports_dir, root / "exports")

    def test_prepare_runtime_creates_a_migrated_user_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = runtime_paths({"JINGWEI_DATA_DIR": temp_dir})
            with patch.dict(os.environ, {}, clear=False):
                repository = prepare_runtime(paths)
                with repository.connect() as connection:
                    version = connection.execute(
                        "SELECT schema_version FROM schema_migrations "
                        "ORDER BY rowid DESC LIMIT 1"
                    ).fetchone()["schema_version"]

                self.assertEqual(version, "0.8")
                self.assertTrue(paths.database.is_file())
                self.assertTrue(paths.exports_dir.is_dir())
                self.assertEqual(os.environ["JINGWEI_EXPORT_DIR"], str(paths.exports_dir))

    def test_smoke_test_covers_page_templates_write_and_word_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / "smoke.json"
            with patch.dict(os.environ, {"JINGWEI_DATA_DIR": temp_dir}, clear=False):
                result = run_smoke_test(report_path)

            self.assertTrue(result["ok"])
            self.assertEqual(result["templates"], 7)
            self.assertTrue(result["model_settings"])
            self.assertTrue(Path(result["word_export"]).read_bytes().startswith(b"PK"))
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), result)


if __name__ == "__main__":
    unittest.main()
