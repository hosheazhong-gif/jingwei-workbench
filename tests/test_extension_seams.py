from __future__ import annotations

import shutil
import json
import tempfile
import unittest
from pathlib import Path

from app.adapters.sqlite_repository import SqliteRepository
from app.application.export_deliverable import export_project
from app.application.round_questions import MAX_QUESTIONS, _template_hints
from app.application.import_sample import import_sample
from app.exporters import default_exporters
from app.projections.report import build_report_projection, build_review_context
from app.projections.templates import build_template_list_projection
from app.application.create_project import create_project, ensure_review_shell
from app.templates.registry import DEFAULT_TEMPLATE_KEY, VERIFICATION_LEVELS, load_template, load_templates


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"
TEMPLATES_ROOT = PROJECT_ROOT / "app/templates"
DEMO_TEMPLATE = TEMPLATES_ROOT / "demo_market_scan/template.json"
DEFAULT_TEMPLATE = TEMPLATES_ROOT / "industry_chain/template.json"
FORBIDDEN_TERMS = ("远川园区", "冷链", "星河优选", "生产型租户")


class ExtensionSeamTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)

    def test_registering_demo_template_adds_no_migrations(self) -> None:
        with self.repository.connect() as connection:
            before = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
        template = load_template("demo_market_scan")
        self.repository.migrate()
        with self.repository.connect() as connection:
            after = connection.execute(
                "SELECT COUNT(*) FROM schema_migrations"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        self.assertEqual(template.key, "demo_market_scan")
        self.assertEqual(
            list(template.recommended_question_labels()),
            ["市场现状", "主要变化", "待核实项"],
        )
        self.assertTrue(template.brief_prompt)
        self.assertEqual(before, after)
        self.assertEqual(before, 9)
        self.assertNotIn("demo_market_scan", tables)

    def test_plain_text_exporter_does_not_change_markdown(self) -> None:
        markdown = export_project(self.repository, self.project_id, "markdown")
        registry = default_exporters()
        self.assertIn("plain_text", registry)
        markdown_again = export_project(
            self.repository, self.project_id, "markdown", exporters=registry
        )
        plain = export_project(self.repository, self.project_id, "plain_text")
        self.assertEqual(markdown["content"], markdown_again["content"])
        self.assertEqual(markdown["block_ids"], ["DB-001", "DB-002", "DB-003", "DB-004"])
        self.assertEqual(plain["block_ids"], markdown["block_ids"])
        self.assertTrue(plain["filename"].endswith(".txt"))
        self.assertIn("内部评审包", plain["content"])
        self.assertIn("项目本体分析页", plain["content"])
        self.assertIn("写入限制", plain["content"])
        self.assertIn("待重审段落", plain["content"])
        self.assertNotEqual(plain["content"], markdown["content"])

    def test_views_still_share_deliverable_block_ids_after_seams(self) -> None:
        load_template("demo_market_scan")
        export_project(self.repository, self.project_id, "plain_text")
        report = build_report_projection(self.repository, self.project_id)
        review = build_review_context(self.repository, "DB-001")
        self.assertEqual(
            {block["id"] for block in report["blocks"]},
            {"DB-001", "DB-002", "DB-003", "DB-004"},
        )
        self.assertEqual(review["block"]["id"], "DB-001")
        self.assertEqual(
            {claim["id"] for claim in review["claims"]},
            {"C-001", "C-002", "C-004", "C-005"},
        )

    def test_demo_template_and_plain_text_exporter_have_no_synthetic_terms(self) -> None:
        demo_text = DEMO_TEMPLATE.read_text(encoding="utf-8")
        # 默认模板也不许再带单个案子的词：PRD 20.6 把它从「清湖低信息售前研究」
        # 改名成通用的「产业链分析」，名字和问法都不能再绑死在一个案子上。
        default_text = DEFAULT_TEMPLATE.read_text(encoding="utf-8")
        exporter_text = (
            PROJECT_ROOT / "app/exporters/plain_text.py"
        ).read_text(encoding="utf-8")
        registry_text = (
            PROJECT_ROOT / "app/templates/registry.py"
        ).read_text(encoding="utf-8")
        for term in FORBIDDEN_TERMS:
            self.assertNotIn(term, demo_text)
            self.assertNotIn(term, default_text)
            self.assertNotIn(term, exporter_text)
            self.assertNotIn(term, registry_text)

    def test_default_template_is_the_industry_chain_framework(self) -> None:
        # PRD 20.6：默认模板 = P-004 已验证的四节问法 + 清湖那条证据纪律，
        # 再按 docs/00 §4.3 对原七层的审计补回「研究对象与边界」（原产业识别）
        # 和「区位与本地条件」（原本地基础层）。这七条会被 round_questions 当
        # template_hints 发给模型，不是摆设，所以内容改动必须有人看见。
        template = load_template("industry_chain_analysis_presales")
        self.assertEqual(template.key, "industry_chain_analysis_presales")
        self.assertEqual(template.name, "产业链分析（低信息售前研究）")
        self.assertEqual(template.execution_strategy_key, "fixed_workflow")
        self.assertTrue(template.brief_prompt)
        labels = list(template.recommended_question_labels())
        self.assertEqual(len(labels), 7)
        for keyword in ("边界", "政策", "研报", "标杆", "产业链", "区位", "宏观市场"):
            self.assertTrue(
                any(keyword in item for item in labels),
                f"默认模板的问题提示里应该有「{keyword}」这一条",
            )
        # 产业图谱不单列成一条（docs/00 §4.2 审计：图谱与产业链边界不清），
        # 它的实质并进产业链那一条：环节、参与者、价值被谁拿走。
        chain = next(item for item in labels if "产业链" in item)
        self.assertIn("参与者", chain)
        self.assertIn("价值", chain)
        # 本地这一段是缺口提示，不是搜索指令：网上搜不到靠谱的本地一手信息，
        # 提示要把人推向现场和客户，而不是推向再搜一次。
        local = next(item for item in labels if "区位" in item)
        self.assertIn("自己", local)
        # 延链机会不进模板（docs/00 §4.2 第 5 条 + 清湖 F-002）：证据不够时
        # 不许在拆问题阶段就诱导模型去想「该往哪延伸」。
        for item in labels:
            self.assertNotIn("延链", item)
        # 每轮问题上限 5 条，模板给的是一份七条的菜单，不是七条都要跑。
        self.assertGreater(len(labels), MAX_QUESTIONS)
        # 旧模板必须真的没了，不是并排放着。容器改不了你磁盘上的目录，
        # 所以这条红了就是在提醒：手工删掉 app\\templates\\synthetic\\ 这个目录。
        self.assertNotIn(
            "case_specific_low_info_presales",
            load_templates(),
            "旧模板还在：请删掉 app/templates/qinghu/ 整个目录（PRD 20.6 改名后它是残留）",
        )

    def test_commercial_dd_desk_template_is_a_second_selectable_type(self) -> None:
        # 第二个真正的新题型：商业尽调案头版。它对「每条结论挂得住证据」的要求
        # 最刚（尽调报告要出依赖函），正好压在经纬的强项上。
        template = load_template("commercial_dd_desk")
        self.assertEqual(template.name, "商业尽调（案头版）")
        self.assertTrue(template.listed)
        labels = list(template.recommended_question_labels())
        self.assertEqual(len(labels), 7)
        for keyword in ("投资逻辑", "市场", "竞对", "客户", "成本"):
            self.assertTrue(
                any(keyword in item for item in labels),
                f"商业尽调模板的问题提示里应该有「{keyword}」这一条",
            )
        # 案头版必须自己说清楚边界：访谈和问卷拿到的东西不在这一段里。
        boundary = next(item for item in labels if "访谈" in item)
        self.assertIn("边界", boundary)
        # 出处纪律条：尽调的每条结论都要挂得住。
        self.assertTrue(any("出处" in item for item in labels))
        self.assertIn("访谈", template.brief_prompt or "")

    def test_every_listed_template_explains_itself(self) -> None:
        # 介绍页只描述已经装进来的模板（PRD 20.10）。内容必须对得上这个模板
        # 真实的问法，不许写套话；样例必须自己声明不是材料。
        listing = build_template_list_projection()
        for item in listing["templates"]:
            self.assertTrue(item["intro"].strip(), item["key"])
            self.assertGreaterEqual(len(item["steps"]), 3, item["key"])
            self.assertTrue(item["when_to_use"], item["key"])
            self.assertTrue(item["when_not_to_use"], item["key"])
            self.assertTrue(item["pitfalls"], item["key"])
            for step in item["steps"]:
                # 每一步都要说清做完的标志，否则「大致几步」还是句空话
                self.assertTrue(step["title"], item["key"])
                self.assertTrue(step["done_when"], item["key"])
            self.assertIn("不是材料", item["example"]["note"])
            # 现场缺陷（docs/20 §6）：样例稿模拟的是工作台的真实输出，那里没有
            # Markdown。星号留在里面会让人以为稿里可以写记号。
            for key in ("brief", "material", "draft"):
                self.assertNotIn(
                    "**", str(item["example"].get(key) or ""),
                    f"{item['key']} 的样例 {key} 里有 Markdown 记号",
                )
            # 流程图必须把「人点收下」那几道关卡画出来——那是这产品最要紧的一条
            gates = [x for x in item["flow"] if x.get("gate")]
            self.assertGreaterEqual(len(gates), 3, item["key"])
            for gate in gates:
                self.assertIn("收下", gate["stage"])
            template = load_template(item["key"])
            self.assertEqual(
                item["question_labels"], list(template.recommended_question_labels())
            )
        # 走查状态和问法来处（PRD 20.12）。模板不止给写它的人用：
        # 没走查过的必须自己说出来，七条问法必须条条说得出来处。
        for item in listing["templates"]:
            # 三档不是两档：「问法试过」和「整条循环走过」不许混成一个已走查。
            self.assertIn(item["verification"], VERIFICATION_LEVELS, item["key"])
            self.assertEqual(
                item["status_label"], VERIFICATION_LEVELS[item["verification"]], item["key"]
            )
            self.assertTrue(item["status_note"].strip(), item["key"])
            if item["verification"] != "loop_walked":
                # 没走完整条循环的，必须自己在说明里承认还差哪一步。
                # 认「还没」太松：那两个字可能出现在任何一句里。改成认
                # 「差的到底是什么」——模型那一步没验，就必须写出来。
                note = item["status_note"]
                self.assertTrue(
                    "模型" in note and ("还没验" in note or "还没走" in note or "没试过" in note),
                    f"{item['key']} 的状态说明没写清还差哪一步：{note[:40]}",
                )
            self.assertEqual(
                len(item["questions"]), len(item["question_labels"]), item["key"]
            )
            for row in item["questions"]:
                self.assertTrue(row["source"].strip(), item["key"])
                self.assertNotEqual(row["source"], "没注明来处", item["key"])
                # 来处要说清是哪来的，不许拿问法自己充数
                self.assertNotEqual(row["source"], row["label"], item["key"])
            # 现场缺陷（docs/20 §6）：删了一条问法，却忘了改「什么时候用它」，
            # 页面上还写着那条已经不存在的证据来源。介绍和问法必须对得上。
            hay = "".join(item["question_labels"])
            for word in ("招聘", "问卷", "访谈"):
                if word in "".join(item["when_to_use"]):
                    self.assertIn(
                        word, hay, f"{item['key']}：介绍说要用「{word}」，七条问法里却没有"
                    )
            # 模板名和问法都不许绑死在某个案子上（第 8.24 节的规矩）
            blob = json.dumps(item, ensure_ascii=False)
            for term in FORBIDDEN_TERMS:
                self.assertNotIn(term, blob, f"{item['key']} 里出现了案子名 {term}")
        # 验得深的排在验得浅的前面：人不细看也先碰到验过的那几个。
        rank = {"loop_walked": 0, "walked_by_hand": 1, "skeleton": 2}
        ranks = [rank[item["verification"]] for item in listing["templates"]]
        self.assertEqual(ranks, sorted(ranks))
        # 试问法时撞出来的来源陷阱要写在页面上：这条循环的硬门槛只管
        # 「原话逐字来自快照」，管不住这个来源本身算不算数。
        self.assertTrue(listing["source_traps"])
        for item in listing["source_traps"]:
            self.assertTrue(item["trap"].strip())
            self.assertTrue(item["why"].strip())
        # 装不进来的活要写清楚，别让人自己撞上去。
        self.assertTrue(listing["out_of_scope"])
        for item in listing["out_of_scope"]:
            self.assertTrue(item["work"].strip())
            self.assertTrue(item["why"].strip())
        # 尽调那份必须把「访谈问卷不在这个模板里」这条边界写在介绍里，
        # 否则人会以为它能做全套尽调。
        dd = next(x for x in listing["templates"] if x["key"] == "commercial_dd_desk")
        self.assertIn("访谈", dd["intro"])
        # 接缝演练用的假模板不进介绍页。
        self.assertFalse(load_template("demo_market_scan").listed)

    def test_template_list_puts_the_system_default_first_and_marks_it(self) -> None:
        # 现场缺陷（docs/20 §6，2026-08-23）：投影按 key 字母排序，
        # commercial_dd_desk 排到了前面，浏览器就默认选中它——人不动下拉框
        # 会静默拿到另一套问法，而不是系统真正的默认模板。
        listing = build_template_list_projection()
        keys = [item["key"] for item in listing["templates"]]
        self.assertEqual(listing["default_key"], DEFAULT_TEMPLATE_KEY)
        self.assertEqual(keys[0], DEFAULT_TEMPLATE_KEY)
        defaults = [item["key"] for item in listing["templates"] if item["is_default"]]
        self.assertEqual(defaults, [DEFAULT_TEMPLATE_KEY])
        # 默认模板必须真的是不传 template_key 时会用的那一份。
        created = create_project(
            self.repository,
            name="没选模板的题",
            original_context="经理说：看看这个产业链缺什么。",
        )
        with self.repository.connect() as connection:
            stored = connection.execute(
                "SELECT template_key FROM projects WHERE id = ?",
                (created["project_id"],),
            ).fetchone()["template_key"]
        self.assertEqual(stored, listing["default_key"])

    def test_template_list_only_offers_real_templates(self) -> None:
        # 新建题目那一栏只列真模板：接缝演练用的假模板显式 listed=false，
        # 不能摆到人面前当选项。
        listing = build_template_list_projection()
        keys = [item["key"] for item in listing["templates"]]
        self.assertIn("industry_chain_analysis_presales", keys)
        self.assertIn("commercial_dd_desk", keys)
        self.assertNotIn("demo_market_scan", keys)
        self.assertFalse(load_template("demo_market_scan").listed)
        for item in listing["templates"]:
            self.assertIn("is_default", item)
            self.assertTrue(item["name"])
            self.assertTrue(item["brief_prompt"])
            self.assertGreater(item["question_hint_count"], 0)
        self.assertIn("不写入问题", listing["limitation"])

    def test_creating_a_project_with_a_chosen_template_uses_its_hints(self) -> None:
        # 选了模板要真的走到拆问题的提示里，不是只在库里存个字符串。
        created = create_project(
            self.repository,
            name="某标的的案头尽调",
            original_context="经理说：先看看这家标的公开材料能撑到哪。",
            template_key="commercial_dd_desk",
        )
        project_id = created["project_id"]
        with self.repository.connect() as connection:
            stored = connection.execute(
                "SELECT template_key FROM projects WHERE id = ?", (project_id,)
            ).fetchone()["template_key"]
        self.assertEqual(stored, "commercial_dd_desk")
        hints = _template_hints(self.repository, project_id)
        self.assertEqual(len(hints), 7)
        self.assertTrue(any("投资逻辑" in item for item in hints))
        # 没选模板时仍然落回产业链分析那套。
        default_project = create_project(
            self.repository,
            name="没选模板的题",
            original_context="经理说：看看这个产业链缺什么。",
        )
        default_hints = _template_hints(self.repository, default_project["project_id"])
        self.assertTrue(any("产业链" in item for item in default_hints))

    def test_legacy_template_key_is_migrated_not_left_broken(self) -> None:
        # 旧库里的项目 template_key 还是 case_specific_low_info_presales。0008 迁移必须
        # 把它改过来，否则 load_template 找不到模板，建占位稿和拆问题都会炸。
        with self.repository.transaction() as connection:
            connection.execute(
                "UPDATE projects SET template_key = 'case_specific_low_info_presales'"
            )
            connection.execute(
                "DELETE FROM schema_migrations WHERE migration_id = ?",
                ("0008_rename_default_template_key",),
            )
        self.repository.migrate()
        with self.repository.connect() as connection:
            keys = {
                row["template_key"]
                for row in connection.execute("SELECT template_key FROM projects")
            }
            name = connection.execute(
                "SELECT name FROM projects WHERE id = 'P-DEMO-001'"
            ).fetchone()["name"]
            text = connection.execute(
                "SELECT current_text FROM deliverable_blocks WHERE id = 'DB-001'"
            ).fetchone()["current_text"]
            status = connection.execute(
                "SELECT verification_status FROM claims WHERE id = 'C-002'"
            ).fetchone()["verification_status"]
        self.assertEqual(keys, {"industry_chain_analysis_presales"})
        # 迁移只动这一列：题目名称、稿、核验状态都不许被改写。
        self.assertEqual(name, "远川食品产业园匿名研究")
        self.assertIn("据客户提供", text)
        self.assertEqual(status, "captured")
        # 改完之后建占位稿这条路要真的走得通，不是只看一眼字符串。
        ensure_review_shell(self.repository, self.project_id)

    def test_removing_demo_template_does_not_break_synthetic_read(self) -> None:
        isolated = Path(self.temp_dir.name) / "templates"
        default_dir = isolated / "industry_chain"
        default_dir.mkdir(parents=True)
        shutil.copy(DEFAULT_TEMPLATE, default_dir / "template.json")
        loaded = load_templates(isolated)
        self.assertIn("industry_chain_analysis_presales", loaded)
        self.assertNotIn("demo_market_scan", loaded)
        report = build_report_projection(self.repository, self.project_id)
        review = build_review_context(self.repository, "DB-001")
        markdown = export_project(self.repository, self.project_id, "markdown")
        self.assertEqual(
            report["project"]["template_key"], "industry_chain_analysis_presales"
        )
        self.assertEqual(review["block"]["id"], "DB-001")
        self.assertIn("项目问题", markdown["content"])


if __name__ == "__main__":
    unittest.main()
