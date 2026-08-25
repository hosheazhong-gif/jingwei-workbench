from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.api.server import dispatch_get, dispatch_post
from app.local_env import load_local_env
from app.model_settings import (
    MODEL_ENV_KEYS,
    ModelSettingsError,
    read_model_settings,
    save_model_settings,
)


class ModelSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.original_env = {key: os.environ.get(key) for key in MODEL_ENV_KEYS}
        for key in MODEL_ENV_KEYS:
            os.environ.pop(key, None)
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_save_is_immediately_active_and_survives_restart(self) -> None:
        result = save_model_settings(
            self.root,
            {"provider": "deepseek", "api_key": "sk-local-test"},
        )
        self.assertTrue(result["api_key_set"])
        self.assertEqual(result["provider"], "deepseek")
        self.assertEqual(result["model"], "deepseek-chat")
        self.assertNotIn("api_key", result)
        self.assertEqual(os.environ["JINGWEI_DRAFT_API_KEY"], "sk-local-test")

        for key in MODEL_ENV_KEYS:
            os.environ.pop(key, None)
        load_local_env(self.root)
        restarted = read_model_settings()
        self.assertTrue(restarted["api_key_set"])
        self.assertEqual(restarted["provider"], "deepseek")

    def test_read_and_http_response_never_return_secret(self) -> None:
        status, saved = dispatch_post(
            self.repository,
            "/settings/model",
            {"provider": "openai", "api_key": "sk-do-not-return"},
        )
        self.assertEqual(status, 200)
        self.assertNotIn("sk-do-not-return", repr(saved))

        status, loaded = dispatch_get(self.repository, "/settings/model")
        self.assertEqual(status, 200)
        self.assertTrue(loaded["api_key_set"])
        self.assertNotIn("api_key", loaded)
        self.assertNotIn("sk-do-not-return", repr(loaded))

    def test_blank_password_keeps_existing_secret(self) -> None:
        save_model_settings(
            self.root,
            {"provider": "deepseek", "api_key": "sk-keep-me"},
        )
        save_model_settings(
            self.root,
            {"provider": "qwen", "url": "", "model": "qwen-plus"},
        )
        self.assertEqual(os.environ["JINGWEI_DRAFT_API_KEY"], "sk-keep-me")
        self.assertIn(
            "JINGWEI_DRAFT_API_KEY=sk-keep-me",
            (self.root / ".env").read_text(encoding="utf-8"),
        )

    def test_remove_key_clears_file_process_and_projection(self) -> None:
        save_model_settings(
            self.root,
            {"provider": "glm", "api_key": "sk-remove-me"},
        )
        result = save_model_settings(
            self.root,
            {"provider": "glm", "clear_api_key": True},
        )
        self.assertFalse(result["api_key_set"])
        self.assertNotIn("JINGWEI_DRAFT_API_KEY", os.environ)
        self.assertNotIn(
            "sk-remove-me", (self.root / ".env").read_text(encoding="utf-8")
        )

    def test_custom_provider_requires_valid_url_and_model(self) -> None:
        with self.assertRaisesRegex(ModelSettingsError, "完整"):
            save_model_settings(
                self.root,
                {
                    "provider": "custom",
                    "api_key": "sk-test",
                    "url": "not-a-url",
                    "model": "demo",
                },
            )
        with self.assertRaisesRegex(ModelSettingsError, "模型名称"):
            save_model_settings(
                self.root,
                {
                    "provider": "custom",
                    "api_key": "sk-test",
                    "url": "https://example.com/v1/chat/completions",
                },
            )

    def test_missing_key_is_rejected_with_user_instruction(self) -> None:
        with self.assertRaisesRegex(ModelSettingsError, "API Key"):
            save_model_settings(self.root, {"provider": "deepseek"})
