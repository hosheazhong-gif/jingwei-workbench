"""代理搜材料：护栏与不变量。

要钉住的不是"代理搜得好不好"，是**它有没有越界**——
越界一次，整个产品的证据纪律就没了。

fixture 沿用 `test_search_materials.py` 的写法：临时库 + 合成样本 + 问题 RQ-01。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.agent_search_materials import (
    MAX_QUERIES_PER_ROUND,
    AgentSearchError,
    agent_search_project_materials,
)
from app.application.import_sample import import_sample

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"
QUESTION_ID = "RQ-01"


class FakeSearch:
    key = "fake"

    def __init__(self, pages: dict[str, list[dict]], fail_on: set[str] | None = None) -> None:
        self.pages = pages
        self.fail_on = fail_on or set()
        self.calls: list[str] = []

    def search(self, query: str):
        self.calls.append(query)
        if query in self.fail_on:
            raise RuntimeError("上游限流")
        return self.pages.get(query, [])


class ScriptedBrain:
    """按剧本逐轮吐字符串。用来精确构造非法输出、不肯停、提前结束。"""

    key = "scripted"

    def __init__(self, script: list[str]) -> None:
        self.script = list(script)
        self.prompts: list[str] = []

    def decide(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.script:
            return self.script.pop(0)
        return json.dumps({"思考": "没词了", "动作": "结束", "理由": "剧本用尽"}, ensure_ascii=False)


def _act(*queries: str) -> str:
    return json.dumps(
        {"思考": "换个口径试试", "动作": "搜索", "检索词": list(queries)}, ensure_ascii=False
    )


_STOP = json.dumps({"思考": "够了", "动作": "结束", "理由": "覆盖到了"}, ensure_ascii=False)


class AgentGuardrailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repository = SqliteRepository(Path(self.temp_dir.name) / "jingwei.sqlite3")
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def _run(self, search, brain, **kwargs):
        return agent_search_project_materials(
            self.repository,
            self.project_id,
            question_id=QUESTION_ID,
            search_adapter=search,
            brain=brain,
            **kwargs,
        )

    # ── 最要紧的一条 ──
    def test_agent_writes_candidates_only(self) -> None:
        """代理只许新增候选。碰了 claim、稿或来源，_assert_unchanged 会当场炸。"""
        search = FakeSearch({"冷链 园区 招商": [{"url": "https://a.example/1", "title": "甲"}]})
        before_sources = self.repository.list_source_ids(self.project_id)

        result = self._run(search, ScriptedBrain([_act("冷链 园区 招商"), _STOP]))

        self.assertEqual(result["added_count"], 1)
        self.assertEqual(self.repository.list_source_ids(self.project_id), before_sources)
        self.assertFalse(result["confirmation"]["source_created"])
        self.assertFalse(result["confirmation"]["deliverable_changed"])
        self.assertFalse(result["confirmation"]["verification_status_changed"])

    def test_illegal_json_consumes_a_round_and_does_not_crash(self) -> None:
        """模型吐出非 JSON：不重试调用，把失败写回上下文，消耗一轮继续。"""
        search = FakeSearch({"甲": [{"url": "https://a.example/1", "title": "甲"}]})

        result = self._run(search, ScriptedBrain(["我觉得应该搜一下冷链园区。", _act("甲"), _STOP]))

        self.assertEqual(search.calls, ["甲"])
        self.assertIn("输出不是合法 JSON", result["transcript"][0]["错误"])

    def test_tool_failure_becomes_an_observation(self) -> None:
        """检索工具报错不打断循环，当成一次观测喂回去。"""
        search = FakeSearch({"乙": [{"url": "https://b.example/1"}]}, fail_on={"甲"})

        result = self._run(search, ScriptedBrain([_act("甲"), _act("乙"), _STOP]))

        self.assertIn("检索失败", result["transcript"][0]["观测"])
        self.assertEqual(result["added_count"], 1)

    def test_round_ceiling_holds_when_the_model_never_stops(self) -> None:
        """模型一直说继续：轮次上限必须兜住，不许无限烧。"""
        brain = ScriptedBrain([_act(f"词{i}") for i in range(20)])

        result = self._run(FakeSearch({}), brain, max_rounds=3)

        self.assertLessEqual(len(result["transcript"]), 3)
        self.assertLessEqual(len(brain.prompts), 3)

    def test_repeated_queries_are_dropped(self) -> None:
        """换词要真换。重复的词不发出去，否则轮次上限等于没有。"""
        search = FakeSearch({"甲": [{"url": "https://a.example/1"}]})

        self._run(search, ScriptedBrain([_act("甲"), _act("甲"), _STOP]))

        self.assertEqual(search.calls, ["甲"])

    def test_queries_per_round_is_capped(self) -> None:
        search = FakeSearch({})

        self._run(search, ScriptedBrain([_act("a", "b", "c", "d"), _STOP]))

        self.assertLessEqual(len(search.calls), MAX_QUERIES_PER_ROUND)

    def test_transcript_records_every_round(self) -> None:
        """A/B 要靠 transcript 说清每条候选是哪一步来的，不能有黑洞。"""
        search = FakeSearch({"甲": [{"url": "https://a.example/1"}]})

        result = self._run(search, ScriptedBrain([_act("甲"), _STOP]))

        self.assertEqual(result["transcript"][0]["检索词"], ["甲"])
        self.assertIn("新增候选 1 条", result["transcript"][0]["观测"])
        self.assertEqual(result["transcript"][-1]["动作"], "结束")

    def test_without_a_model_it_says_so_instead_of_pretending(self) -> None:
        with self.assertRaises(AgentSearchError) as caught:
            self._run(FakeSearch({}), None)
        self.assertIn("还没接模型", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
