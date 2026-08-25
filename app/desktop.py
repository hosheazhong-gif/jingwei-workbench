from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
import tempfile
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from app import __version__
from app.adapters.sqlite_repository import SqliteRepository
from app.api.server import ReadOnlyHttpServer
from app.local_env import load_local_env


APP_TITLE = "经纬咨询决策工作台"
DEFAULT_PORT = 8765
_MUTEX_NAME = "Local\\JingweiConsultingWorkbench"
_ERROR_ALREADY_EXISTS = 183
_IDYES = 6
_IDNO = 7
_MB_OK = 0x00000000
_MB_YESNOCANCEL = 0x00000003
_MB_ICONERROR = 0x00000010
_MB_ICONINFORMATION = 0x00000040


@dataclass(frozen=True)
class RuntimePaths:
    data_dir: Path
    database: Path
    exports_dir: Path


def runtime_paths(environ: dict[str, str] | None = None) -> RuntimePaths:
    env = os.environ if environ is None else environ
    configured = str(env.get("JINGWEI_DATA_DIR") or "").strip()
    if configured:
        data_dir = Path(configured).expanduser()
    elif sys.platform == "win32":
        base = str(env.get("LOCALAPPDATA") or "").strip()
        data_dir = Path(base) / "Jingwei" if base else Path.home() / "AppData" / "Local" / "Jingwei"
    elif sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "Jingwei"
    else:
        data_dir = Path(str(env.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")) / "jingwei"
    return RuntimePaths(
        data_dir=data_dir,
        database=data_dir / "jingwei.sqlite3",
        exports_dir=data_dir / "exports",
    )


def prepare_runtime(paths: RuntimePaths) -> SqliteRepository:
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.exports_dir.mkdir(parents=True, exist_ok=True)
    os.environ["JINGWEI_DATA_DIR"] = str(paths.data_dir)
    load_local_env(paths.data_dir)
    os.environ["JINGWEI_EXPORT_DIR"] = str(paths.exports_dir)
    repository = SqliteRepository(paths.database)
    repository.migrate()
    return repository


def _open_folder(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        webbrowser.open(path.as_uri())


def _acquire_windows_mutex() -> tuple[int | None, bool]:
    if sys.platform != "win32":
        return None, False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(handle), ctypes.get_last_error() == _ERROR_ALREADY_EXISTS


def _release_windows_mutex(handle: int | None) -> None:
    if handle is not None and sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle(handle)


def _message_box(text: str, flags: int) -> int:
    if sys.platform != "win32":
        return 0
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    user32.MessageBoxW.restype = ctypes.c_int
    return int(user32.MessageBoxW(None, text, APP_TITLE, flags))


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def run_smoke_test(report_path: Path | None = None) -> dict[str, Any]:
    paths = runtime_paths()
    repository = prepare_runtime(paths)
    server = ReadOnlyHttpServer(repository, host="127.0.0.1", port=0)
    server.start()
    try:
        with urlopen(server.origin + "/", timeout=10) as response:
            home_body = response.read().decode("utf-8")
            home_ok = (
                response.status == 200
                and "经纬" in home_body
                and "model-settings-dialog" in home_body
            )
        with urlopen(server.origin + "/templates", timeout=10) as response:
            templates = json.loads(response.read().decode("utf-8"))["templates"]
        settings_status, settings_saved = _post_json(
            server.origin + "/settings/model",
            {
                "provider": "deepseek",
                "api_key": "jingwei-package-smoke-key",
            },
        )
        with urlopen(server.origin + "/settings/model", timeout=10) as response:
            settings_loaded = json.loads(response.read().decode("utf-8"))
        settings_ok = bool(
            settings_status == 200
            and settings_saved.get("api_key_set")
            and settings_loaded.get("api_key_set")
            and settings_loaded.get("provider") == "deepseek"
            and "api_key" not in settings_saved
            and "api_key" not in settings_loaded
        )
        create_status, created = _post_json(
            server.origin + "/projects",
            {
                "name": "应用包启动验收",
                "original_context": "验证封装后的数据库、模板、页面和导出。",
                "decision_question": "应用包能否独立运行？",
                "deliverable": "一份可编辑的验收稿",
                "template_key": "industry_chain_analysis_presales",
            },
        )
        project_id = str(created["project_id"])
        export_status, exported = _post_json(
            server.origin + f"/projects/{project_id}/exports/word",
            {"save_to_folder": True},
        )
        saved = Path(str(exported["saved_path"]))
        result = {
            "ok": bool(
                home_ok
                and len(templates) == 7
                and settings_ok
                and create_status == 201
                and export_status == 200
                and saved.is_file()
                and saved.read_bytes().startswith(b"PK")
            ),
            "version": __version__,
            "templates": len(templates),
            "model_settings": settings_ok,
            "project_id": project_id,
            "database": str(paths.database),
            "word_export": str(saved),
        }
    finally:
        server.stop()
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _run_controller() -> int:
    mutex_handle: int | None = None
    server: ReadOnlyHttpServer | None = None
    try:
        mutex_handle, already_running = _acquire_windows_mutex()
        if already_running:
            webbrowser.open(f"http://127.0.0.1:{DEFAULT_PORT}/", new=1)
            return 0

        paths = runtime_paths()
        repository = prepare_runtime(paths)
        try:
            server = ReadOnlyHttpServer(repository, host="127.0.0.1", port=DEFAULT_PORT)
        except OSError:
            _message_box(
                f"本机端口 {DEFAULT_PORT} 已被其他程序占用。\n\n"
                "请关闭占用端口的程序后再启动经纬。",
                _MB_OK | _MB_ICONERROR,
            )
            return 2
        server.start()
        url = server.origin + "/"
        webbrowser.open(url, new=1)
        if sys.platform != "win32":
            server._thread.join()
            return 0
        while True:
            action = _message_box(
                "经纬正在本机运行，工作台已经在默认浏览器中打开。\n\n"
                f"本机地址：{url}\n"
                f"数据目录：{paths.data_dir}\n\n"
                "选择“是”：重新打开工作台\n"
                "选择“否”：打开数据与导出目录\n"
                "选择“取消”：安全退出经纬",
                _MB_YESNOCANCEL | _MB_ICONINFORMATION,
            )
            if action == _IDYES:
                webbrowser.open(url, new=1)
            elif action == _IDNO:
                _open_folder(paths.data_dir)
            else:
                break
        return 0
    except Exception as exc:
        _message_box(f"经纬启动失败：\n{exc}", _MB_OK | _MB_ICONERROR)
        return 1
    finally:
        if server is not None and server._thread.is_alive():
            server.stop()
        _release_windows_mutex(mutex_handle)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--smoke-report", type=Path)
    args, _ = parser.parse_known_args(argv)
    if args.smoke_test:
        result = run_smoke_test(args.smoke_report)
        return 0 if result["ok"] else 1
    return _run_controller()


if __name__ == "__main__":
    raise SystemExit(main())
