from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import __version__


DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"
SPEC_PATH = PROJECT_ROOT / "packaging" / "Jingwei.spec"
QUICK_START = PROJECT_ROOT / "packaging" / "快速开始.txt"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_zip(executable: Path) -> Path:
    archive = DIST_DIR / f"Jingwei-Windows-x64-v{__version__}.zip"
    root_name = f"Jingwei-v{__version__}"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.write(executable, f"{root_name}/Jingwei.exe")
        bundle.write(QUICK_START, f"{root_name}/快速开始.txt")
        license_path = PROJECT_ROOT / "LICENSE"
        if license_path.is_file():
            bundle.write(license_path, f"{root_name}/LICENSE.txt")
    return archive


def _smoke_test(executable: Path) -> dict[str, object]:
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jingwei-package-", dir=BUILD_DIR) as temp_dir:
        root = Path(temp_dir)
        report = root / "smoke-report.json"
        env = os.environ.copy()
        env["JINGWEI_DATA_DIR"] = str(root / "data")
        subprocess.run(
            [str(executable), "--smoke-test", "--smoke-report", str(report)],
            # Run outside the source tree so bundled assets cannot be masked by
            # files that only exist in a developer checkout.
            cwd=root,
            env=env,
            check=True,
            timeout=180,
        )
        result = json.loads(report.read_text(encoding="utf-8"))
        if not result.get("ok"):
            raise RuntimeError(f"packaged smoke test failed: {result}")
        return result


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows application packages must be built on Windows.")

    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(DIST_DIR),
            "--workpath",
            str(BUILD_DIR / "pyinstaller"),
            str(SPEC_PATH),
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )
    executable = DIST_DIR / "Jingwei.exe"
    if not executable.is_file():
        raise RuntimeError("PyInstaller completed without creating dist/Jingwei.exe")

    smoke = _smoke_test(executable)
    archive = _write_zip(executable)
    checksum = _sha256(archive)
    checksum_path = archive.with_suffix(archive.suffix + ".sha256")
    checksum_path.write_text(f"{checksum}  {archive.name}\n", encoding="ascii")

    direct_checksum = _sha256(executable)
    executable_checksum = executable.with_suffix(executable.suffix + ".sha256")
    executable_checksum.write_text(
        f"{direct_checksum}  {executable.name}\n", encoding="ascii"
    )
    print(
        json.dumps(
            {
                "version": __version__,
                "executable": str(executable),
                "archive": str(archive),
                "archive_sha256": checksum,
                "smoke": smoke,
            },
            # GitHub's Windows runner may expose a cp1252 console. Keep the
            # machine-readable build summary printable on every code page.
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
