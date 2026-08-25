from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_ROOT / "scripts"
BANNER = SCRIPTS / "print_windows_banner.py"
UTF8_BOM = b"\xef\xbb\xbf"


class WindowsScriptEncodingTest(unittest.TestCase):
    def test_powershell_scripts_start_with_utf8_bom(self) -> None:
        paths = sorted(SCRIPTS.glob("*.ps1"))
        self.assertTrue(paths)
        for path in paths:
            raw = path.read_bytes()
            self.assertTrue(
                raw.startswith(UTF8_BOM),
                f"{path.name} must be UTF-8 with BOM so Windows PowerShell 5.1 does not misread it as GBK",
            )

    def test_powershell_scripts_are_ascii_after_bom(self) -> None:
        for path in sorted(SCRIPTS.glob("*.ps1")):
            body = path.read_bytes()[len(UTF8_BOM) :]
            body.decode("ascii")

    def test_serve_banner_explains_not_a_website_without_a_key(self) -> None:
        env = os.environ.copy()
        env["JINGWEI_DRAFT_API_KEY"] = ""
        env.pop("JINGWEI_DRAFT_PROVIDER", None)
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(BANNER), "serve"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertIn("这不是网站", result.stdout)
        self.assertIn("http://127.0.0.1:8000/", result.stdout)
        self.assertIn("http://127.0.0.1:8000/?project=P-DEMO-001", result.stdout)
        self.assertIn("先关掉再开", result.stdout)
        self.assertIn("JINGWEI_DRAFT_API_KEY", result.stdout)
        self.assertIn(".env", result.stdout)
        self.assertIn("deepseek", result.stdout)
        self.assertIn("未设置 JINGWEI_DRAFT_API_KEY", result.stdout)

    def test_serve_banner_mentions_provider_without_printing_the_key(self) -> None:
        env = os.environ.copy()
        env["JINGWEI_DRAFT_API_KEY"] = "sk-test-should-not-appear"
        env["JINGWEI_DRAFT_PROVIDER"] = "deepseek"
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(BANNER), "serve"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )
        self.assertIn("deepseek", result.stdout)
        self.assertIn("会出候选", result.stdout)
        self.assertNotIn("sk-test-should-not-appear", result.stdout)
        self.assertNotIn("未设置 JINGWEI_DRAFT_API_KEY", result.stdout)
