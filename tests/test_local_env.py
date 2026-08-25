from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.local_env import load_local_env, update_local_env


class LocalEnvTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)

    def test_loads_missing_keys_and_ignores_comments(self) -> None:
        (self.root / ".env").write_text(
            "# comment\nJINGWEI_DRAFT_PROVIDER=deepseek\nJINGWEI_DRAFT_API_KEY=sk-from-file\n",
            encoding="utf-8",
        )
        os.environ.pop("JINGWEI_DRAFT_PROVIDER", None)
        os.environ.pop("JINGWEI_DRAFT_API_KEY", None)
        self.addCleanup(lambda: os.environ.pop("JINGWEI_DRAFT_PROVIDER", None))
        self.addCleanup(lambda: os.environ.pop("JINGWEI_DRAFT_API_KEY", None))
        load_local_env(self.root)
        self.assertEqual(os.environ["JINGWEI_DRAFT_PROVIDER"], "deepseek")
        self.assertEqual(os.environ["JINGWEI_DRAFT_API_KEY"], "sk-from-file")

    def test_does_not_override_existing_env(self) -> None:
        (self.root / ".env").write_text(
            "JINGWEI_DRAFT_API_KEY=sk-from-file\n",
            encoding="utf-8",
        )
        os.environ["JINGWEI_DRAFT_API_KEY"] = "already-set"
        self.addCleanup(lambda: os.environ.pop("JINGWEI_DRAFT_API_KEY", None))
        load_local_env(self.root)
        self.assertEqual(os.environ["JINGWEI_DRAFT_API_KEY"], "already-set")

    def test_updates_selected_keys_without_removing_unrelated_lines(self) -> None:
        (self.root / ".env").write_text(
            "# keep this\nUNRELATED=value\nJINGWEI_DRAFT_API_KEY=old-key\n",
            encoding="utf-8",
        )
        update_local_env(
            self.root,
            {
                "JINGWEI_DRAFT_PROVIDER": "deepseek",
                "JINGWEI_DRAFT_API_KEY": "new-key",
            },
        )
        content = (self.root / ".env").read_text(encoding="utf-8")
        self.assertIn("# keep this", content)
        self.assertIn("UNRELATED=value", content)
        self.assertIn("JINGWEI_DRAFT_PROVIDER=deepseek", content)
        self.assertIn("JINGWEI_DRAFT_API_KEY=new-key", content)
        self.assertNotIn("old-key", content)

    def test_none_removes_managed_key(self) -> None:
        (self.root / ".env").write_text(
            "JINGWEI_DRAFT_API_KEY=old-key\nUNRELATED=value\n",
            encoding="utf-8",
        )
        update_local_env(self.root, {"JINGWEI_DRAFT_API_KEY": None})
        content = (self.root / ".env").read_text(encoding="utf-8")
        self.assertNotIn("JINGWEI_DRAFT_API_KEY", content)
        self.assertIn("UNRELATED=value", content)
