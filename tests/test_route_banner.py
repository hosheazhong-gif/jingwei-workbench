from __future__ import annotations

import re
import unittest
from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "app/api/server.py"
_ROUTE = re.compile(r'^_[A-Z_]+_PATH = re\.compile\(\s*\n?\s*r?"\^([^"]+)\$"', re.M)
_BANNER = re.compile(r'"(?:GET|POST|DELETE) (/[^"]*)"')


def _norm(path: str) -> str:
    """占位符叫什么名字不算差别：{id} 和 {claim_id} 是同一个位置。"""
    return re.sub(r"\{[a-z_]+\}", "{}", path.replace("([^/]+)", "{id}"))


class RouteBannerTest(unittest.TestCase):
    """启动时打印的那张路由表不许比真实路由少。

    现场缺陷（docs/20 §6，2026-08-23）：那张表是手写的，已经漏掉了
    `/templates`、`DELETE /sources/{id}`、`/projects/{id}/brief`。
    开机第一眼看到的东西说了假话，比没有这张表更糟。
    """

    def test_banner_lists_every_route(self) -> None:
        source = SERVER.read_text(encoding="utf-8")
        routes = {_norm(match) for match in _ROUTE.findall(source)}
        banner = {_norm(match) for match in _BANNER.findall(source)}
        self.assertTrue(routes, "没解析出任何路由，测试本身失效了")
        missing = sorted(routes - banner)
        self.assertEqual(missing, [], f"启动横幅漏了这些路由：{missing}")


if __name__ == "__main__":
    unittest.main()
