# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path.cwd().resolve()

datas = [
    (str(project_root / "frontend" / "report"), "frontend/report"),
    (str(project_root / "app" / "migrations"), "app/migrations"),
    (str(project_root / "app" / "templates"), "app/templates"),
]

a = Analysis(
    [str(project_root / "app" / "desktop.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Jingwei",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=str(project_root / "packaging" / "windows_version_info.txt"),
)
