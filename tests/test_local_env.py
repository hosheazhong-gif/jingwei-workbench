from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.local_env import load_local_env


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
