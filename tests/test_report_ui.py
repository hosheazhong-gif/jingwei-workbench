from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app.adapters.sqlite_repository import SqliteRepository
from app.api import ReadOnlyHttpServer
from app.api.server import resolve_static_file
from app.application.import_sample import import_sample
from app.projections.report import build_report_projection, build_review_context
from app.projections.workbench import build_workbench_projection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PATH = PROJECT_ROOT / "samples/synthetic_case/consulting_fixture_v0.1.json"


class ReportUiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = Path(self.temp_dir.name) / "jingwei.sqlite3"
        self.repository = SqliteRepository(self.database_path)
        self.repository.migrate()
        self.project_id = import_sample(self.repository, SAMPLE_PATH)
        self.server = ReadOnlyHttpServer(self.repository, host="127.0.0.1", port=0)
        self.server.start()
        self.addCleanup(self.server.stop)

    def test_home_page_is_workbench_shell_without_copied_conclusions(self) -> None:
        status, content_type, body = self._get_text("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn("给经理的稿", body)
        self.assertIn("class=\"split\"", body)
        self.assertIn("col-body", body)
        self.assertIn("本轮问题", body)
        self.assertIn("材料", body)
        self.assertIn("这轮要决定", body)
        self.assertIn("全部题目", body)
        self.assertIn("新建题目", body)
        self.assertIn("整理题目", body)
        self.assertIn("导出整理稿", body)
        # 两份导出并排：经理版是整理稿，详细版供内部核对
        self.assertIn("导出详细版", body)
        self.assertIn('id="export-detailed"', body)
        self.assertIn("/app.js", body)
        self.assertIn('id="home"', body)
        self.assertIn('id="bench"', body)
        self.assertIn("home-toolbar", body)
        self.assertNotIn("处理这一段", body)
        self.assertNotIn("售前研究账本", body)
        self.assertNotIn("study-nav", body)
        self.assertNotIn("1 本题", body)
        self.assertNotIn("过不过", body)
        self.assertNotIn("60%+", body)
        self.assertNotIn("生产型租户占比可以作为客户提供信息进入报告", body)

    def test_browser_script_reads_workbench_and_keeps_ledger_writes(self) -> None:
        status, content_type, script = self._get_text("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)
        self.assertIn("/workbench", script)
        self.assertIn("/draft-revision", script)
        self.assertIn("/revisions/adopt", script)
        self.assertIn("/research-questions/", script)
        self.assertIn("/progress", script)
        self.assertIn("写这一节", script)
        self.assertIn("按材料再写一版", script)
        self.assertIn("从快照扒原话", script)
        self.assertIn("/excerpt-draft", script)
        self.assertIn("归到这条问题", script)
        self.assertIn("还没标对应哪条问题", script)
        self.assertIn("改这一节", script)
        self.assertIn("收回上一版", script)
        self.assertIn("去掉这一节", script)
        self.assertIn("加入文件", script)
        self.assertIn("这轮不用", script)
        self.assertIn("正在写", script)
        self.assertIn("将发送", script)
        self.assertIn("收下", script)
        self.assertIn("丢掉", script)
        self.assertIn("这轮够用了", script)
        self.assertIn("PROGRESS_CYCLE", script)
        self.assertNotIn("progressButtons", script)
        self.assertIn("paragraphSpans", script)
        self.assertIn("block-body", script)
        self.assertIn("再加一条问题", script)
        self.assertIn("按这句话拆问题", script)
        self.assertIn("/round-questions/", script)
        self.assertIn("/rounds/close", script)
        # 收口是换轮，跟「再加一条问题」分开放，也写清后果
        self.assertIn("round-close-button", script)
        self.assertIn("收口第", script)
        self.assertIn("word_detailed", script)
        # 轮次文件夹：稿栏和材料栏都把收口过的那一轮收成一个总文件夹
        self.assertIn("archivedDraftFolders", script)
        self.assertIn("round-folder-body", script)
        self.assertIn("mat:round:", script)
        self.assertIn("draft:round:", script)
        self.assertIn("groupFolded", script)
        # 标题不再一律截断，光标停上去看整句
        self.assertIn("questionTitle", script)
        # replaceChildren 会把 null 渲染成文字「null」，空白题上一直在漏
        self.assertIn("keepNodes", script)
        self.assertNotIn("mandateRoot.replaceChildren(", script)
        self.assertIn("这一节挂着的原话", script)
        self.assertIn("archived_rounds", script)
        self.assertIn("看快照", script)
        self.assertIn("打开链接", script)
        self.assertIn("original_url", script)
        self.assertIn("/snapshot", script)
        self.assertIn("改这条", script)
        # 抽不出正文的快照不给两个键；卡片上直接说只能打开链接
        self.assertIn("can_scrape_snapshot", script)
        self.assertIn("snapshot_note", script)
        # 未标材料可勾选后一次归入，走批量接口；仍只认人点名的 id
        self.assertIn("/materials/question", script)
        self.assertIn("把勾上的", script)
        # 上一轮的材料按「第 N 轮 · 问题」分组，不再显示成没标过
        self.assertIn("archivedLabels", script)
        # 候选默认就画在稿上，必须能对上一版
        self.assertIn("对一下上一版", script)
        self.assertIn("prior-body", script)
        # 收下之后又挂了原话，要说出来
        self.assertIn("material_since_draft", script)
        # 粘进来的长原话先给开头，点开才铺全；库里的字不动
        self.assertIn("看全文", script)
        self.assertIn("EXCERPT_PREVIEW_CHARS", script)
        self.assertIn("这轮先不用", script)
        self.assertIn("这轮再用", script)
        self.assertIn("/defer", script)
        self.assertIn("question_id", script)
        self.assertIn("preview_checks", script)
        self.assertIn("未挂来源的说法", script)
        self.assertIn("selectedMaterialId", script)
        self.assertIn("/brief", script)
        self.assertIn("记下这段原文", script)
        self.assertIn("/claims", script)
        self.assertIn("factual_claim", script)
        self.assertIn("material-title", script)
        self.assertIn("/unlink", script)
        self.assertIn("这节先不用", script)
        self.assertIn("claim_sources", script)
        self.assertNotIn("认识类型", script)
        self.assertIn("sourceDetails", script)
        self.assertIn("excerpts", script)
        self.assertIn("改为新加入", script)
        self.assertNotIn("替代哪份", script)
        self.assertIn("确认去掉", script)
        self.assertIn("整理题目", script)
        self.assertIn("homeTidying", script)
        self.assertIn("更多", script)
        self.assertIn("const extra = []", script)
        self.assertIn("jingwei-bench-cols", script)
        self.assertIn("col-dragging", script)
        self.assertIn("initColumnSplit", script)
        self.assertIn("selectedExcerptClaimId", script)
        self.assertIn("这一节叫什么", script)
        self.assertIn("点一下换成", script)
        self.assertNotIn('title: "未命名一节"', script)
        self.assertIn("bumpQuestionDraft", script)
        self.assertIn("mandate-text", script)
        self.assertIn("/title", script)
        self.assertIn("block-title", script)
        self.assertIn("editingTitleBlockId", script)
        self.assertIn("节名不能空着", script)
        self.assertIn("supersedes_source_id", script)
        self.assertIn("加入文件", script)
        self.assertIn("加入链接", script)
        self.assertIn("按这轮问题搜", script)
        self.assertIn("/material-search", script)
        self.assertIn("export-word", script)
        self.assertIn("未挂来源", script)
        self.assertIn("再试一次", script)
        self.assertIn("home-row", script)
        self.assertIn("renderFailedBench", script)
        self.assertIn("再运行一次 serve", script)
        # ——以下为 workbench28~33 各刀的验收点，缺一条就说明那一刀没真正落盘——
        self.assertIn("can_scrape_snapshot", script)
        self.assertIn("snapshot_note", script)
        self.assertIn("/materials/question", script)
        self.assertIn("把勾上的", script)
        self.assertIn("archivedLabels", script)
        self.assertIn("对一下上一版", script)
        self.assertIn("prior-body", script)
        self.assertIn("material_since_draft", script)
        self.assertIn("看全文", script)
        self.assertIn("EXCERPT_PREVIEW_CHARS", script)
        self.assertIn("short_label", script)
        self.assertIn("question_short_label", script)
        self.assertIn("full-question", script)
        self.assertIn("groupHeading", script)
        self.assertIn("collapsedGroups", script)
        self.assertIn("jingwei-bench-folds", script)
        self.assertIn("这轮不用的", script)
        self.assertIn("拿回来", script)
        self.assertIn("/restore", script)
        self.assertIn("set_aside", script)
        self.assertIn("fold-count", script)
        self.assertIn("blockGist", script)
        self.assertIn("expandAllBlocks", script)
        self.assertIn("全部展开", script)
        self.assertIn("贴经理反馈", script)
        self.assertIn("/manager-feedback", script)
        self.assertIn("feedback_as_evidence", script)
        self.assertIn("lookbackRound", script)
        self.assertIn("roundTabs", script)
        self.assertIn("round-tab", script)
        self.assertIn("renderLookbackQuestions", script)
        self.assertIn("renderLookbackMaterials", script)
        self.assertIn("lookbackQuestionCard", script)
        self.assertIn("这一轮没用上的", script)
        self.assertIn("/rounds/reopen", script)
        self.assertIn("roundReopenControls", script)
        self.assertIn("mandateDraftButton", script)
        self.assertIn("/round-decision/draft", script)
        self.assertIn("回到当前轮", script)
        self.assertIn("renderLookbackDraft", script)
        # 搜索前要提醒用户手里还有未归题的材料。
        self.assertIn("templatePicker", script)
        self.assertIn("template_key", script)
        self.assertIn("/templates", script)
        self.assertIn("按哪类活来拆问题", script)
        # 建完题目之后模板也要一直看得见：列表每行 + 顶栏
        self.assertIn("home-template", script)
        self.assertIn("project-template", script)
        self.assertIn("template_name", script)
        # 导出顺手在题目文件夹留一份
        self.assertIn("save_to_folder", script)
        # 模板介绍页：只描述已装模板，样例要跟正文分得开
        self.assertIn("renderGuide", script)
        self.assertIn("guideFlow", script)
        self.assertIn("flow-node", script)
        self.assertIn("guide-example", script)
        self.assertIn("mode-guide", script)
        self.assertIn("模板都是干什么的", script)
        # 稿的重复只提示不拦截
        self.assertIn("repeated_phrases", script)
        # 数字清单：默认收起，找不到出处的要跳出来
        self.assertIn("numberManifest", script)
        self.assertIn("number_manifest", script)
        self.assertIn("找不到出处", script)
        # 默认项要显式选中，不许只靠「默认排第一」碰运气
        self.assertIn("templateDefaultKey", script)
        # 去掉材料：先点一次再确认一次，被拒的理由要原样说给人听
        self.assertIn("removeSource", script)
        self.assertIn("pendingSourceRemovalId", script)
        self.assertIn("去掉这份", script)
        self.assertIn("确认去掉", script)
        self.assertIn("untaggedMaterialCount", script)
        self.assertIn("search-reminder", script)
        self.assertIn("搜之前先看看能不能用上", script)
        # 声明齐不齐：少一个 let，整页就白（feedbackOpen 就这么崩过）
        for name in (
            "expandAllBlocks",
            "feedbackOpen",
            "lookbackRound",
            "pendingDecision",
            "bulkPicks",
            "comparingBlockId",
            "expandedExcerpts",
            "collapsedGroups",
        ):
            self.assertRegex(script, r"let\s+" + name + r"\s*=")
        self.assertNotIn("过不过", script)
        self.assertNotIn("pane-tab", script)
        self.assertNotIn("P-DEMO-001", script)
        self.assertIn('params.get("project")', script)

    def test_browser_script_is_not_python_syntax(self) -> None:
        script = (PROJECT_ROOT / "frontend/report/app.js").read_text(encoding="utf-8")
        # 走查状态要三处都露出来：介绍页标题旁、选模板的选项文字里、
        # 以及选中尚未走查模板时当场给出的提示。
        for needle in (
            "guide-status",
            "loop_walked",
            "guide-status-note",
            "source_traps",
            "untried-note",
            "guide-question-source",
            "guide-out",
        ):
            self.assertIn(needle, script, f"app.js 少了 {needle}")
        # 选模板时改提示条不许重画整个表单——会把人已经打好的字清空。
        self.assertNotIn("onChange: function () { renderCreateForm(); }", script)
        pythonish = re.findall(r"(?m)^\s*if\s+[A-Za-z_]\w*\s*===", script)
        self.assertEqual(
            pythonish,
            [],
            "Python-style if in app.js prevents the report page from rendering",
        )

    def test_shell_stylesheet_has_three_columns(self) -> None:
        status, content_type, css = self._get_text("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn("css", content_type)
        self.assertIn("font-size: 17px", css)
        # 顶栏两个导出按钮必须同尺寸：那个按钮只有 id 没有 class 时，
        # 统一尺寸的规则命不中它，一大一小（流水账第 2 条）。
        self.assertIn("#export-word,\nbutton.primary,", css)
        # 设计基线：色板和字号只在 :root 定义一次，规则里不写死。
        for token in (
            "--bg: #f7f7f5",
            "--paper: #ffffff",
            "--ink: #37352f",
            "--fs-body",
            "--fs-note",
            "--fs-meta",
            "--lh-body",
        ):
            self.assertIn(token, css)
        # hidden 必须真的隐藏：#bench 的 display:flex 曾经把它压过去，
        # 首页和工作台因此同屏。
        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)
        # 四档按钮共用一套尺寸，并排的两个不能一大一小。
        self.assertIn("button.secondary", css)
        # 写死颜色只许出现在 :root 里。
        head, _, body = css.partition("}")
        import re as _re
        stray = sorted(set(_re.findall(r"#[0-9a-fA-F]{6}", body)))
        self.assertEqual(stray, [], f"规则里还有写死颜色：{stray}")
        self.assertIn(".bench", css)
        self.assertIn(".marked", css)
        self.assertIn(".home-row", css)
        self.assertIn(".draft-col", css)
        self.assertIn(".block-body p", css)
        self.assertIn(".col-body", css)
        self.assertIn(".deferred-heading", css)
        self.assertIn("col-resize", css)
        self.assertIn(".split", css)
        self.assertIn(".prior-body", css)
        self.assertIn("button.linky", css)
        self.assertIn("button.group-heading", css)
        self.assertIn(".material.set-aside", css)
        self.assertIn(".fold-count", css)
        self.assertIn(".block.folded", css)
        self.assertIn(".lookback-banner", css)
        # 走查状态必须有自己的样式：没走查过的模板不能跟走查过的长得一样。
        self.assertIn(".guide-status", css)
        # 三档各有各的样子：整条循环走过的才给绿色，问法试过是中间档。
        self.assertIn(".guide-status.loop_walked", css)
        self.assertIn(".guide-status.questions_probed", css)
        self.assertIn(".untried-note", css)
        # 研报标题可能很长且不带空格；
        # 空格，出处那一列写成 flex: 0 0 auto 就把上下文挤成 0 宽、还捅出了
        # 面板右边。这两列都必须自己会缩、会省略。
        for block, must in (
            (".number-context", ("flex: 1 1 55%", "text-overflow: ellipsis")),
            (".number-where", ("flex: 0 1 11rem", "text-overflow: ellipsis")),
            (".impact-why", ("overflow: hidden", "text-overflow: ellipsis")),
        ):
            rule = css.split(block + " {", 1)[1].split("}", 1)[0]
            for needle in must:
                self.assertIn(needle, rule, f"{block} 少了 {needle}")
            self.assertNotIn("flex: 0 0 auto", rule, f"{block} 不许写死不缩")
        self.assertIn("button.round-tab", css)
        self.assertNotIn("study-nav", css)
        self.assertNotIn("pane-tab", css)

    def test_static_path_cannot_escape_frontend_directory(self) -> None:
        self.assertIsNone(resolve_static_file("/../app/migrations/0001_schema_v0_1.sql"))
        self.assertIsNone(resolve_static_file("/app.js/../../samples/synthetic_case/consulting_fixture_v0.1.json"))
        status, payload = self._get_json("/../app/cli.py")
        self.assertEqual(status, 404)
        self.assertIn("error", payload)

    def test_ui_and_api_still_share_deliverable_block_ids(self) -> None:
        page_status, _, page = self._get_text("/")
        api_status, workbench = self._get_json(
            f"/projects/{self.project_id}/workbench"
        )
        review_status, review = self._get_json(
            "/deliverable-blocks/DB-001/review-context"
        )
        self.assertEqual(page_status, 200)
        self.assertEqual(api_status, 200)
        self.assertEqual(review_status, 200)
        self.assertEqual(
            workbench, build_workbench_projection(self.repository, self.project_id)
        )
        self.assertEqual(review, build_review_context(self.repository, "DB-001"))
        self.assertIn("给经理的稿", page)
        self.assertIn("DB-001", {block["id"] for block in workbench["blocks"]})
        self.assertEqual(
            {block["id"] for block in workbench["blocks"]},
            {block["id"] for block in build_report_projection(self.repository, self.project_id)["blocks"]},
        )
        self.assertEqual(review["block"]["id"], "DB-001")

    def _get_text(self, path: str) -> tuple[int, str, str]:
        with urlopen(self.server.origin + path) as response:
            content_type = response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            return response.status, content_type, response.read().decode(charset)

    def _get_json(self, path: str) -> tuple[int, dict]:
        request = Request(self.server.origin + path, method="GET")
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            payload = json.loads(error.read().decode("utf-8"))
            error.close()
            return error.code, payload


if __name__ == "__main__":
    unittest.main()
