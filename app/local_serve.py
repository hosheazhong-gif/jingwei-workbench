from __future__ import annotations

import errno
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def is_address_in_use(error: OSError) -> bool:
    winerror = getattr(error, "winerror", None)
    if winerror == 10048:
        return True
    codes = {errno.EADDRINUSE}
    wsa = getattr(errno, "WSAEADDRINUSE", None)
    if wsa is not None:
        codes.add(wsa)
    return error.errno in codes


def is_jingwei_serve_command(command_line: str) -> bool:
    lowered = " ".join((command_line or "").lower().split())
    return "app.cli" in lowered and " serve" in f" {lowered}"


def parse_netstat_listening_pids(output: str, port: int) -> list[int]:
    pids: list[int] = []
    suffix = f":{port}"
    for raw in output.splitlines():
        parts = raw.split()
        if len(parts) < 5:
            continue
        protocol, local, state, pid_text = parts[0], parts[1], parts[3], parts[-1]
        if protocol.upper() not in {"TCP", "TCPv6"}:
            continue
        if state.upper() not in {"LISTENING", "LISTEN"} and state != "侦听":
            continue
        if not local.endswith(suffix):
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid > 0:
            pids.append(pid)
    return pids


def recycle_jingwei_listeners(port: int) -> bool:
    """Stop a previous ``app.cli serve`` on this port. Do not touch other programs."""
    stopped = False
    for pid in sorted(set(_listening_pids(port))):
        command = _command_line(pid)
        if not is_jingwei_serve_command(command):
            continue
        _stop_pid(pid)
        stopped = True
    if stopped:
        time.sleep(0.6)
    return stopped


def _listening_pids(port: int) -> list[int]:
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    return parse_netstat_listening_pids(completed.stdout or "", port)


def _command_line(pid: int) -> str:
    if sys.platform == "win32":
        return _windows_command_line(pid)
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
            "utf-8", "replace"
        )
    except OSError:
        return ""


def _windows_command_line(pid: int) -> str:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    return (completed.stdout or "").strip()


def _stop_pid(pid: int) -> None:
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/F"],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return
    os.kill(pid, signal.SIGTERM)
