"""Print Chinese banners for Windows PowerShell scripts.

Windows PowerShell 5.1 reads UTF-8 scripts as the system ANSI code page
unless they have a BOM. Keep user-facing Chinese out of ``.ps1`` bodies and
print it from Python, which uses the console encoding.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.local_env import load_local_env


def _configure_stdout() -> None:
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.reconfigure(encoding=encoding, errors="replace")
    except (AttributeError, OSError, ValueError):
        pass


def print_serve_banner() -> None:
    print("这不是网站。先打开本机首页，再进题目。")
    print("首页: http://127.0.0.1:8000/")
    print("匿名合成样本: http://127.0.0.1:8000/?project=P-DEMO-001")
    print("工作流入口仍是 PowerShell / app.cli，交付是 Word。")
    print("8000 若被旧看稿进程占用，这次会先关掉再开。")
    print("密钥可写在项目根目录 .env，不要提交仓库。")
    if os.environ.get("JINGWEI_DRAFT_API_KEY"):
        provider = os.environ.get("JINGWEI_DRAFT_PROVIDER") or "openai"
        print(f"已看到密钥，服务={provider}：「请模型先拟」会出候选，仍不会改左边。")
    else:
        print(
            "未设置 JINGWEI_DRAFT_API_KEY：「请模型先拟」会说明还没接模型。"
            "DeepSeek 可设 JINGWEI_DRAFT_PROVIDER=deepseek。"
        )


def print_demo_banner(database_path: str, export_path: str) -> None:
    print("")
    print("这不是网站。经纬是本机上的研究工作流，交付是 Word，不是打开一个网址。")
    print("刚才做的：导入远川园区样本（预置初稿，不是模型写的），并导出内部稿。")
    print(f"数据库: {database_path}")
    print(f"Word: {export_path}")
    print("")
    print("接下来在 PowerShell 里做事，例如：")
    print(f'  py -3.12 -m app.cli --db "{database_path}" list-projects --plain')
    print("浏览器只是看稿，不是产品首页：")
    print("  .\\scripts\\serve_readonly.ps1")
    print("脚本会打开看稿页。若 8000 已被旧看稿进程占用，会先关掉再开。")


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    load_local_env(_PROJECT_ROOT)
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("usage: print_windows_banner.py serve|demo [database word]", file=sys.stderr)
        return 2
    kind = args[0]
    if kind == "serve":
        print_serve_banner()
        return 0
    if kind == "demo":
        if len(args) < 3:
            print("usage: print_windows_banner.py demo <database> <word>", file=sys.stderr)
            return 2
        print_demo_banner(args[1], args[2])
        return 0
    print(f"unknown banner: {kind}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
