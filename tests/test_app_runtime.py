from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import __version__
from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.api.server import clear_desktop_controller, register_desktop_controller


class AppRuntimeEndpointsTest(unittest.TestCase):
    """版本随时看得见；退出和心跳交给页面，不再靠弹窗吊着命。

    现场缺陷（docs/20 §6，2026-08-26）：桌面版原来用一个 Windows 弹窗当生命线，
    「取消」既是唯一让弹窗消失的办法、也正好是关掉经纬的选项。同一天还踩到：
    旧进程占着端口时，新进程只是打开浏览器，人看到旧版页面却无从判断。
    """

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(clear_desktop_controller)
        repository = SqliteRepository(Path(self.temp_dir.name) / "j.sqlite3")
        repository.migrate()
        self.server = ReadOnlyHttpServer(repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def _get(self, path: str) -> tuple[int, dict]:
        with urlopen(self.server.origin + path, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def _post(self, path: str) -> tuple[int, dict]:
        request = Request(
            self.server.origin + path,
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            return error.code, json.loads(error.read().decode("utf-8"))

    def test_version_is_always_readable_even_from_the_command_line(self) -> None:
        status, payload = self._get("/app/info")
        self.assertEqual(status, 200)
        self.assertEqual(payload["version"], __version__)
        # 命令行方式跑的时候没有桌面控制器，页面就不显示退出那几个按钮
        self.assertFalse(payload["desktop"])
        self.assertIsNone(payload["data_dir"])

    def test_desktop_only_endpoints_do_not_exist_without_a_controller(self) -> None:
        for path in ("/app/heartbeat", "/app/quit", "/app/data-folder"):
            with self.subTest(path=path):
                status, payload = self._post(path)
                self.assertEqual(status, 404)
                self.assertIn("桌面版", payload["error"])

    def test_heartbeat_quit_and_folder_reach_the_desktop_controller(self) -> None:
        beats: list[int] = []
        quits: list[int] = []
        folders: list[int] = []
        register_desktop_controller(
            data_dir="D:/somewhere/Jingwei",
            on_heartbeat=lambda: beats.append(1),
            on_quit=lambda: quits.append(1),
            on_open_folder=lambda: folders.append(1),
        )
        status, payload = self._get("/app/info")
        self.assertTrue(payload["desktop"])
        self.assertEqual(payload["data_dir"], "D:/somewhere/Jingwei")

        self.assertEqual(self._post("/app/heartbeat")[0], 200)
        self.assertEqual(self._post("/app/heartbeat")[0], 200)
        self.assertEqual(len(beats), 2)

        status, payload = self._post("/app/quit")
        self.assertEqual(status, 200)
        self.assertIn("已退出", payload["message"])
        self.assertEqual(len(quits), 1)

        self.assertEqual(self._post("/app/data-folder")[0], 200)
        self.assertEqual(len(folders), 1)


class DesktopHasNoBlockingDialogLoopTest(unittest.TestCase):
    def test_the_lifetime_no_longer_hangs_on_a_message_box(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "app" / "desktop.py").read_text(
            encoding="utf-8"
        )
        # 弹窗只许用来报错。用它当生命线，就会出现「让它消失的唯一办法正好是
        # 关掉程序」这种事。
        self.assertNotIn("_MB_YESNOCANCEL", source)
        self.assertNotIn("选择“取消”：安全退出经纬", source)
        self.assertIn("register_desktop_controller", source)
        self.assertIn("_IDLE_EXIT_SECONDS", source)
        # 端口被占用、启动失败这两处报错弹窗要留着
        self.assertIn("已被其他程序占用", source)
        # 旧进程占着端口时要说清是哪一版，不能默默打开旧页面
        self.assertIn("_running_version", source)


if __name__ == "__main__":
    unittest.main()
