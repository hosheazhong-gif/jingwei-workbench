from __future__ import annotations

import errno
import unittest

from app.local_serve import (
    is_address_in_use,
    is_jingwei_serve_command,
    parse_netstat_listening_pids,
)


class LocalServeRecycleTest(unittest.TestCase):
    def test_recognizes_jingwei_serve_and_ignores_other_python(self) -> None:
        self.assertTrue(
            is_jingwei_serve_command(
                r'C:\Python\python.exe -m app.cli --db var\jingwei-demo.sqlite3 serve --port 8000'
            )
        )
        self.assertFalse(
            is_jingwei_serve_command(r"C:\Python\python.exe -m unittest discover -s tests")
        )
        self.assertFalse(is_jingwei_serve_command("nginx.exe"))
        self.assertFalse(is_jingwei_serve_command(""))

    def test_parses_listening_pids_from_netstat(self) -> None:
        output = "\n".join(
            [
                "  TCP    127.0.0.1:8000         0.0.0.0:0              LISTENING       29792",
                "  TCP    127.0.0.1:8000         0.0.0.0:0              侦听            30001",
                "  TCP    127.0.0.1:8000         127.0.0.1:51234        ESTABLISHED     111",
                "  TCP    127.0.0.1:443          0.0.0.0:0              LISTENING       8",
            ]
        )
        self.assertEqual(parse_netstat_listening_pids(output, 8000), [29792, 30001])

    def test_address_in_use_detects_windows_bind_error(self) -> None:
        error = OSError(errno.EADDRINUSE, "Address already in use")
        error.winerror = 10048
        self.assertTrue(is_address_in_use(error))
        self.assertFalse(is_address_in_use(OSError(errno.EPERM, "denied")))


if __name__ == "__main__":
    unittest.main()
