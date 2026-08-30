"""scripts/ 下每个入口都要能直接 `python scripts/xxx.py` 跑起来。

现场缺陷（2026-08-30）：新写的 `scripts/ab_search.py` 连栽两次——
先是漏了 `sys.path` 引导，`import app` 直接 ModuleNotFoundError；
补上之后又漏了 `load_local_env`，于是 `cli.py` 读得到 `.env` 里的密钥、
这个脚本读不到，每开一个终端都要重设环境变量。

两次都是同一个病：**同一件事在两个地方各写一遍，其中一处忘了同步。**
跟 docs/20 §6 里 README 模板名对不上代码、启动横幅漏列路由是同一类。
所以照同样的办法处理：写成不变量，让它自己红。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_ROOT = PROJECT_ROOT / "scripts"

# 需要模型或搜索密钥的脚本，必须自己读 .env；纯本地脚本不强求。
NEEDS_KEYS = {"ab_search.py"}


def _scripts() -> list[Path]:
    return sorted(p for p in SCRIPTS_ROOT.glob("*.py") if not p.name.startswith("_"))


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imports_app(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "app":
            return True
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] == "app" for alias in node.names):
                return True
    return False


class ScriptEntrypointTest(unittest.TestCase):
    def test_there_is_at_least_one_script(self) -> None:
        """空目录会让下面几条静默通过。"""
        self.assertTrue(_scripts(), "scripts/ 下没有 .py，这条测试形同虚设")

    def test_scripts_that_import_app_bootstrap_sys_path(self) -> None:
        """直接 python scripts/xxx.py 跑时 sys.path[0] 是 scripts/，不是项目根。"""
        for path in _scripts():
            source = _source(path)
            if not _imports_app(ast.parse(source)):
                continue
            with self.subTest(script=path.name):
                self.assertIn(
                    "sys.path.insert",
                    source,
                    f"{path.name} 引了 app，但没做 sys.path 引导——"
                    "照抄 run_test_shard.py 顶上那四行",
                )
                self.assertIn("parents[1]", source, f"{path.name} 的项目根算错了")

    def test_scripts_that_need_keys_load_local_env(self) -> None:
        """cli.py 调了 load_local_env，脚本不调就读不到 .env 里的密钥。"""
        for path in _scripts():
            if path.name not in NEEDS_KEYS:
                continue
            with self.subTest(script=path.name):
                self.assertIn(
                    "load_local_env",
                    _source(path),
                    f"{path.name} 要用密钥，但没调 load_local_env——"
                    "用户得每开一个终端重设一次环境变量",
                )

    def test_every_script_compiles(self) -> None:
        for path in _scripts():
            with self.subTest(script=path.name):
                ast.parse(_source(path), filename=str(path))


class TemplateKeyTest(unittest.TestCase):
    """模板目录名与 template_key 的关系要说得清楚。

    现场缺陷（2026-08-30）：用命令行建题目时按目录名填 `--template industry_chain`，
    报「未找到模板 industry_chain」——真实 key 是 `industry_chain_analysis_presales`，
    写在 template.json 里。命令行当时没有任何地方能查。
    修法不是强行让两者相等（目录名短一点有它的道理），是补 `list-templates` 入口。
    """

    def test_cli_exposes_a_way_to_look_up_template_keys(self) -> None:
        source = (PROJECT_ROOT / "app" / "cli.py").read_text(encoding="utf-8")
        self.assertIn(
            "list-templates",
            source,
            "CLI 必须能查模板 key，否则用户只能猜目录名",
        )

    def test_default_template_key_actually_exists(self) -> None:
        from app.templates.registry import DEFAULT_TEMPLATE_KEY, load_templates

        self.assertIn(
            DEFAULT_TEMPLATE_KEY,
            load_templates(),
            "默认模板 key 指向一个不存在的模板，新建题目会当场炸",
        )

    def test_every_template_declares_a_known_verification_level(self) -> None:
        from app.templates.registry import VERIFICATION_LEVELS, load_templates

        for key, template in load_templates().items():
            with self.subTest(template=key):
                self.assertIn(template.verification, VERIFICATION_LEVELS)


if __name__ == "__main__":
    unittest.main()
