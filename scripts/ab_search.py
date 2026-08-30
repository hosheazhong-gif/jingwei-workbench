"""A/B 对照：固定 Workflow vs 单 Agent，同一条问题、同一个搜索适配器。

两种跑法：

    # 用合成样本（只验证程序跑得通，测不出业务价值）
    python scripts/ab_search.py --sample samples/synthetic_case/consulting_fixture_v0.1.json \\
                                --question RQ-01

    # 用真题（真正能下结论的那种）——指向经纬的真实库，脚本会各复制一份副本再跑，
    # 你的项目数据一个字都不会被改
    python scripts/ab_search.py --db "%LOCALAPPDATA%\\Jingwei\\jingwei.sqlite3" \\
                                --project P-00X --question RQ-0X \\
                                --out docs/25_AB原始记录.md

**第一次跑栽过的三个坑，现在都堵上了：**

1. **候选上限**。默认 8 太低，代理第 1 轮就顶格退出，第二轮根本不发生——
   换词这个唯一卖点没机会展示。`--max-candidates` 两组统一设（默认 16）。
2. **种子检索词**。不给种子的话两组从第一轮就搜不同的词，比出来的是"两套问法"
   而不是"两种执行策略"。现在 B 组第 1 轮沿用 A 组那批词，之后才自己换。
3. **两组各跑一份独立副本**。共用一个库的话第二组会因为候选已存在而少写一堆。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# 跟 scripts/run_test_shard.py 同一套引导：直接 python scripts/xxx.py 跑的时候，
# sys.path[0] 是 scripts/ 而不是项目根，import app 会找不到。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.adapters.sqlite_repository import SqliteRepository  # noqa: E402
from app.application import search_materials  # noqa: E402
from app.local_env import load_local_env  # noqa: E402
from app.application.agent_search_materials import (  # noqa: E402
    agent_search_project_materials,
)
from app.application.import_sample import import_sample  # noqa: E402
from app.application.search_materials import search_project_materials  # noqa: E402

DEFAULT_MAX_CANDIDATES = 16


def _fresh(
    *, sample: Path | None, db: Path | None, project_id: str | None
) -> tuple[SqliteRepository, str, tempfile.TemporaryDirectory]:
    """每组一份全新副本。真实库只读复制，绝不在原库上跑。"""
    holder = tempfile.TemporaryDirectory()
    target = Path(holder.name) / "jingwei.sqlite3"
    if db is not None:
        shutil.copy2(db, target)
        repository = SqliteRepository(target)
        repository.migrate()
        if not project_id:
            raise SystemExit("用 --db 时必须同时给 --project")
        if not repository.has_project(project_id):
            raise SystemExit(f"这个库里没有项目 {project_id}")
        return repository, project_id, holder
    repository = SqliteRepository(target)
    repository.migrate()
    return repository, import_sample(repository, sample), holder


def _urls(result: dict[str, Any]) -> list[str]:
    return [str(item.get("url") or "") for item in result.get("added") or []]


def _load_keys(db: Path | None) -> None:
    """跟 cli.py 同一套：从 .env 读密钥，省得每开一个终端都要重设环境变量。

    两处都读，先读数据目录（打包版「连接模型」写在那儿），再读仓库根（从源码跑时）。
    `load_local_env` 不覆盖已存在的变量，所以先读的优先。
    """
    if db is not None:
        load_local_env(Path(db).resolve().parent)
    load_local_env(PROJECT_ROOT)


def run(args: argparse.Namespace) -> dict[str, Any]:
    _load_keys(args.db)

    from app.adapters.agent_brain import resolve_agent_brain
    from app.adapters.http_search import resolve_search_adapter

    brain = resolve_agent_brain()
    if brain is None:
        raise SystemExit(
            "还没接模型。三选一：\n"
            "  1) 打开经纬点「连接模型」填 API Key（会写进数据目录的 .env）\n"
            f"  2) 在 {PROJECT_ROOT / '.env'} 里加一行 JINGWEI_DRAFT_API_KEY=你的key\n"
            "  3) 本次临时用：$env:JINGWEI_DRAFT_API_KEY=\"你的key\"\n"
            "另外按需设 JINGWEI_DRAFT_PROVIDER（openai / deepseek / kimi / qwen / glm）。"
        )

    cap = args.max_candidates
    common = {"sample": args.sample, "db": args.db, "project_id": args.project}
    out: dict[str, Any] = {"上限": cap}

    # ── A 组：固定 Workflow ──
    repository, project_id, holder = _fresh(**common)
    original_cap = search_materials.MAX_NEW_CANDIDATES
    original_break = search_materials.STOP_AFTER_FIRST_PRODUCTIVE_QUERY
    search_materials.MAX_NEW_CANDIDATES = cap  # 两组必须同一个上限，否则不可比
    # 固定流程默认第一条出结果就不搜后面几条。对照时关掉：不关的话 A 永远只跑一条词，
    # 两组的差距有一半来自"跑了几条词"，不是"换不换词"，比出来的东西没意义。
    search_materials.STOP_AFTER_FIRST_PRODUCTIVE_QUERY = not args.a_runs_all_queries
    try:
        started = time.monotonic()
        a = search_project_materials(
            repository, project_id, question_id=args.question,
            search_adapter=resolve_search_adapter(),
        )
    finally:
        search_materials.MAX_NEW_CANDIDATES = original_cap
        search_materials.STOP_AFTER_FIRST_PRODUCTIVE_QUERY = original_break
    seed = list(a.get("queries") or [])
    out["A"] = {
        "策略": "固定 Workflow" + ("（跑完全部种子词）" if args.a_runs_all_queries else "（第一条出结果即停）"),
        "检索次数": len(seed),
        "轮数": 1,
        "候选数": a.get("added_count", 0),
        "耗时秒": round(time.monotonic() - started, 2),
        "检索词": seed,
        "候选URL": _urls(a),
    }
    holder.cleanup()

    # ── B 组：单 Agent，第 1 轮沿用 A 的种子词 ──
    repository, project_id, holder = _fresh(**common)
    b = agent_search_project_materials(
        repository, project_id, question_id=args.question,
        search_adapter=resolve_search_adapter(), brain=brain,
        max_rounds=args.rounds, max_candidates=cap, seed_queries=seed,
    )
    out["B"] = {
        "策略": f"单 Agent（上限 {args.rounds} 轮，第 1 轮用 A 的种子词）",
        "检索次数": len(b.get("queries") or []),
        "轮数": b.get("rounds_used", 0),
        "候选数": b.get("added_count", 0),
        "耗时秒": b.get("elapsed_seconds", 0),
        "撞上限": b.get("hit_candidate_cap", False),
        "检索词": b.get("queries") or [],
        "候选URL": _urls(b),
        "transcript": b.get("transcript") or [],
    }
    holder.cleanup()

    a_urls, b_urls = set(out["A"]["候选URL"]), set(out["B"]["候选URL"])
    out["重合"] = {
        "两组都找到": sorted(a_urls & b_urls),
        "只有A找到": sorted(a_urls - b_urls),
        "只有B找到": sorted(b_urls - a_urls),
    }
    # B 换词换出来的（种子之外的词搜到的），才是这次实验真正要看的东西
    out["B换词后新增"] = [q for q in out["B"]["检索词"] if q not in seed]
    return out


def render(data: dict[str, Any], args: argparse.Namespace) -> str:
    a, b = data["A"], data["B"]
    source = f"真实库 {args.db}｜项目 {args.project}" if args.db else f"合成样本 {args.sample}"
    warn = []
    if b.get("撞上限"):
        warn.append(
            f"> ⚠️ **B 组撞了候选上限（{data['上限']}）才停的，不是模型判断够了。**"
            "本轮的轮数与换词表现都被上限压住了，结论要打折看。"
        )
    if not data["B换词后新增"]:
        warn.append(
            "> ⚠️ **B 组没有换出任何新词**——它跑的其实就是固定流程。"
            "这一轮比不出两种策略的差别。"
        )
    blocked = sum(
        1 for step in b.get("transcript") or []
        if "拦截" in str(step.get("观测", "")) or "检索失败" in str(step.get("观测", ""))
    )
    if blocked:
        warn.append(
            f"> ⚠️ **B 组有 {blocked} 轮的检索被拦截或失败。**换词根本没拿到结果，"
            "这一轮测的是搜索后端扛不扛得住连续请求，不是策略好不好。"
            "先配 `JINGWEI_SEARCH_PROVIDER=brave` + `JINGWEI_SEARCH_API_KEY` 再重跑。"
        )
    if not data["重合"]["两组都找到"]:
        warn.append(
            "> ⚠️ **两组零重合。**同题同引擎正常应有相当比例重合，"
            "零重合通常说明两边搜的根本不是同一批词——先检查种子有没有生效。"
        )

    lines = [
        "# A/B 原始记录：固定 Workflow vs 单 Agent",
        "",
        f"> 问题：{args.question}｜{source}",
        f"> 两组同一搜索适配器、同一候选上限（{data['上限']}）、各跑在独立副本上；"
        "B 组第 1 轮沿用 A 组种子词，之后才换词。",
        "> 按 docs/11 §5 三维度记录。**首轮不预设百分比门槛。**",
        "",
    ]
    if warn:
        lines += ["## 本轮有效性警告", ""] + warn + [""]

    lines += [
        "## 自动测到的部分",
        "",
        "| | A 固定 Workflow | B 单 Agent |",
        "|---|---|---|",
        f"| 检索次数 | {a['检索次数']} | {b['检索次数']} |",
        f"| 轮数 | {a['轮数']} | {b['轮数']} |",
        f"| 候选数 | {a['候选数']} | {b['候选数']} |",
        f"| 耗时（秒） | {a['耗时秒']} | {b['耗时秒']} |",
        "",
        f"**B 换词后新增的检索词**（{len(data['B换词后新增'])} 条）："
        + ("、".join(data["B换词后新增"]) or "（无）"),
        "",
        "## 必须人工填的部分",
        "",
        "**这半张表才是决定要不要上 Agent 的依据。**",
        "",
        "| | A | B |",
        "|---|---|---|",
        "| 打得开的比例 |  |  |",
        "| **人工排除了几条** |  |  |",
        "| 覆盖到问题的哪些侧面 |  |  |",
        "| 从结果到可讨论的复核时间 |  |  |",
        "",
        "## 候选重合情况",
        "",
        f"- 两组都找到：{len(data['重合']['两组都找到'])} 条",
        f"- 只有 A 找到：{len(data['重合']['只有A找到'])} 条",
        f"- **只有 B 找到：{len(data['重合']['只有B找到'])} 条**",
        "",
        "> 「只有 B 找到」里，有几条是人工会留下的？**这是 Agent 唯一真正的加分项。**",
        "> 如果这里全是噪声，结论就是不上。",
        "",
        "### 只有 B 找到的",
        "",
    ]
    lines += [f"- {u}" for u in data["重合"]["只有B找到"]] or ["-（无）"]
    lines += ["", "### 只有 A 找到的", ""]
    lines += [f"- {u}" for u in data["重合"]["只有A找到"]] or ["-（无）"]
    lines += [
        "", "## B 组过程转写", "", "```json",
        json.dumps(b.get("transcript") or [], ensure_ascii=False, indent=2), "```", "",
        "## 结论（人工写）", "",
        "按 docs/11 §6：**固定 Workflow 已能稳定完成的，不为展示效果升级为 Agent。**", "",
        "- [ ] Agent 明显更好 → 上线，写进 PRD",
        "- [ ] 打平 → 不上线，固定流程保留",
        "- [ ] Agent 更差 → 不上线，并记下它在什么任务上不适用",
        "- [ ] **本轮无效** → 记下是哪个设计缺陷导致的，改了重跑", "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="固定 Workflow vs 单 Agent 的检索对照")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--sample", type=Path, help="合成样本 JSON（只验证跑得通）")
    group.add_argument("--db", type=Path, help="经纬真实库路径（只读复制，不改原库）")
    parser.add_argument("--project", help="用 --db 时的项目 ID")
    parser.add_argument("--question", default="RQ-01")
    parser.add_argument("--rounds", type=int, default=4)
    parser.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    parser.add_argument(
        "--a-runs-all-queries",
        action="store_true",
        default=True,
        help="A 组跑完全部种子词（对照默认开）；加 --a-stops-early 恢复线上默认行为",
    )
    parser.add_argument(
        "--a-stops-early",
        dest="a_runs_all_queries",
        action="store_false",
        help="A 组第一条出结果就停，跟线上默认行为一致",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    data = run(args)
    text = render(data, args)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}")
        print(f"  A 候选 {data['A']['候选数']} 条 / B 候选 {data['B']['候选数']} 条 "
              f"/ 只有 B 找到 {len(data['重合']['只有B找到'])} 条")
    else:
        print(text)


if __name__ == "__main__":
    main()
