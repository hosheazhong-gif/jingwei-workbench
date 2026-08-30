from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from app import SCHEMA_VERSION
from app.adapters.sqlite_repository import SqliteRepository
from app.api import serve_readonly_api
from app.application.add_block import add_deliverable_block
from app.application.attach_claim import attach_claim_to_block
from app.application.attach_finding import attach_finding_to_block
from app.application.attach_option import attach_option_to_block
from app.application.verify_claim import update_claim_verification
from app.application.capture_source import capture_local_source
from app.application.candidate_source import (
    capture_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.create_project import create_project
from app.application.search_materials import search_project_materials
from app.application.draft_suggestion import (
    adopt_model_suggestion,
    dismiss_model_suggestion,
    draft_block_revision,
    draft_model_suggestions,
)
from app.application.demo_walk import run_blank_walk
from app.application.export_deliverable import export_project
from app.application.import_sample import import_sample
from app.application.review_block import (
    adopt_revision,
    propose_block_revision,
    record_override_decision,
    record_review_decision,
)
from app.application.update_brief import update_brief
from app.local_env import load_local_env
from app.templates.registry import VERIFICATION_LEVELS, load_templates
from app.projections.brief import build_brief_projection
from app.projections.candidates import build_candidate_source_projection
from app.projections.impact import build_impact_preview
from app.projections.projects import build_project_list_projection
from app.projections.report import build_report_projection, build_review_context


def main() -> None:
    load_local_env(Path(__file__).resolve().parents[1])
    parser = argparse.ArgumentParser(description="经纬咨询决策工作台命令行工具")
    parser.add_argument("--db", type=Path, required=True, help="SQLite 数据库路径")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("migrate", help="执行显式 schema 迁移")
    import_parser = subparsers.add_parser("import-sample", help="导入当前 schema 样本")
    import_parser.add_argument("sample", type=Path)
    list_parser = subparsers.add_parser("list-projects", help="列出已建题目")
    list_parser.add_argument("--plain", action="store_true", help="只印题目名称")
    templates_parser = subparsers.add_parser(
        "list-templates",
        help="列出可用模板的 key、名称和验到什么程度",
    )
    templates_parser.add_argument(
        "--all",
        action="store_true",
        help="连不在新建题目里列出的接缝演练模板一起印",
    )
    create_parser = subparsers.add_parser("create-project", help="新建空白题目与任务边界")
    create_parser.add_argument("--name", required=True)
    create_parser.add_argument("--original-context", required=True)
    create_parser.add_argument("--decision-question")
    create_parser.add_argument("--deliverable")
    create_parser.add_argument("--template")
    create_parser.add_argument(
        "--question",
        action="append",
        dest="questions",
        help="本轮必要问题，可重复",
    )
    add_block = subparsers.add_parser("add-block", help="人工新增内部稿段落，不生成主张")
    add_block.add_argument("project_id")
    add_block.add_argument("--title", required=True)
    add_block.add_argument("--text", required=True)
    add_block.add_argument("--restriction")
    attach_claim = subparsers.add_parser(
        "attach-claim", help="人工挂接摘录与主张到段落，不改写内部稿"
    )
    attach_claim.add_argument("deliverable_block_id")
    attach_claim.add_argument("--source", required=True)
    attach_claim.add_argument("--excerpt", required=True)
    attach_claim.add_argument("--text", required=True)
    attach_claim.add_argument(
        "--type",
        dest="epistemic_type",
        required=True,
        choices=["factual_claim", "inference", "assumption", "judgment"],
    )
    attach_claim.add_argument("--locator")
    attach_claim.add_argument("--context-limit")
    attach_claim.add_argument(
        "--client-provided",
        action="store_true",
        help="标为客户提供，不等于外部独立核实",
    )
    attach_claim.add_argument(
        "--macro",
        action="store_true",
        help="宏观市场材料，不单独证明项目需求",
    )
    attach_finding = subparsers.add_parser(
        "attach-finding", help="人工挂接综合判断到段落，不改写内部稿"
    )
    attach_finding.add_argument("deliverable_block_id")
    attach_finding.add_argument("--text", required=True)
    attach_finding.add_argument(
        "--claim",
        action="append",
        dest="claim_ids",
        help="支持主张编号，可重复",
    )
    attach_finding.add_argument("--alternative")
    attach_finding.add_argument(
        "--confidence",
        choices=["low", "medium", "high", "low_to_medium"],
        default="low",
    )
    attach_option = subparsers.add_parser(
        "attach-option", help="人工挂接待验证方向到段落，不改写内部稿"
    )
    attach_option.add_argument("deliverable_block_id")
    attach_option.add_argument("--text", required=True)
    attach_option.add_argument(
        "--status",
        choices=["candidate", "needs_evidence", "retained", "deferred", "excluded"],
        default="candidate",
    )
    draft_suggestion = subparsers.add_parser(
        "draft-suggestion",
        help="请模型先拟总判断或方向；未接模型时拒绝编造，不改内部稿",
    )
    draft_suggestion.add_argument("deliverable_block_id")
    draft_revision = subparsers.add_parser(
        "draft-revision",
        help="请模型先拟一版改稿候选；未接模型时拒绝编造，不立刻改内部稿",
    )
    draft_revision.add_argument("deliverable_block_id")
    adopt_suggestion = subparsers.add_parser(
        "adopt-suggestion", help="采用先拟候选，挂到段落，仍不改核验"
    )
    adopt_suggestion.add_argument("suggestion_id")
    dismiss_suggestion = subparsers.add_parser(
        "dismiss-suggestion", help="这版先不用，不改内部稿"
    )
    dismiss_suggestion.add_argument("suggestion_id")
    verify_claim = subparsers.add_parser(
        "verify-claim", help="更新主张核验状态，不改写内部稿"
    )
    verify_claim.add_argument("deliverable_block_id")
    verify_claim.add_argument("claim_id")
    verify_claim.add_argument(
        "--status",
        required=True,
        choices=[
            "captured",
            "source_checked",
            "corroborated",
            "conflicted",
            "stale",
            "unverifiable",
            "excluded",
        ],
    )
    report_parser = subparsers.add_parser("report", help="输出报告优先投影")
    report_parser.add_argument("project_id")
    brief_parser = subparsers.add_parser("brief", help="输出任务边界投影")
    brief_parser.add_argument("project_id")
    save_brief = subparsers.add_parser("save-brief", help="更新任务边界，不改写内部稿")
    save_brief.add_argument("project_id")
    save_brief.add_argument("--original-context")
    save_brief.add_argument("--decision-question")
    save_brief.add_argument("--deliverable")
    review_parser = subparsers.add_parser("review-context", help="输出段落审查侧栏投影")
    review_parser.add_argument("deliverable_block_id")
    serve_parser = subparsers.add_parser("serve", help="启动本地 HTTP")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument(
        "--open",
        metavar="URL",
        help="服务起来后打开本机看稿地址，不是做成网站",
    )
    capture_parser = subparsers.add_parser("capture-source", help="捕获本地文件为新 Source")
    capture_parser.add_argument("project_id")
    capture_parser.add_argument("path", type=Path)
    capture_parser.add_argument("--title")
    capture_parser.add_argument("--supersedes")
    candidate_parser = subparsers.add_parser(
        "capture-candidate", help="收录网页候选，未打开前不是可引用来源"
    )
    candidate_parser.add_argument("project_id")
    candidate_parser.add_argument("url")
    candidate_parser.add_argument("--title")
    candidate_parser.add_argument("--note")
    open_candidate = subparsers.add_parser(
        "open-candidate", help="记录人工打开网页候选"
    )
    open_candidate.add_argument("candidate_id")
    promote_candidate = subparsers.add_parser(
        "promote-candidate", help="打开后升为可引用 Source，不抽取摘录"
    )
    promote_candidate.add_argument("candidate_id")
    promote_candidate.add_argument("--title")
    list_candidates = subparsers.add_parser("list-candidates", help="列出网页候选")
    list_candidates.add_argument("project_id")
    search_materials = subparsers.add_parser(
        "search-materials",
        help="按本轮问题搜公开网页，写入候选；打开后才升为来源",
    )
    search_materials.add_argument("project_id")
    search_materials.add_argument("--question-id")
    impact_parser = subparsers.add_parser("impact-preview", help="输出临时影响范围")
    impact_parser.add_argument("source_id")
    review_write = subparsers.add_parser("review-block", help="保存段落评审")
    review_write.add_argument("deliverable_block_id")
    review_write.add_argument("--action", required=True, choices=["approve", "modify", "exclude"])
    review_write.add_argument("--reason")
    review_write.add_argument("--proposed-text")
    override_block = subparsers.add_parser("override-block", help="段落级带风险推进")
    override_block.add_argument("deliverable_block_id")
    override_block.add_argument(
        "--handling", required=True, choices=["assumption", "exclude", "scenario"]
    )
    override_block.add_argument("--reason")
    override_block.add_argument("--review-trigger")
    override_block.add_argument("--proposed-text")
    override_project = subparsers.add_parser("override-project", help="项目级带风险推进")
    override_project.add_argument("project_id")
    override_project.add_argument(
        "--handling", required=True, choices=["assumption", "exclude", "scenario"]
    )
    override_project.add_argument("--reason")
    override_project.add_argument("--review-trigger")
    propose_parser = subparsers.add_parser(
        "propose-revision", help="保存段落改稿为候选，不立刻替换内部稿"
    )
    propose_parser.add_argument("deliverable_block_id")
    propose_parser.add_argument("--text", required=True)
    adopt_parser = subparsers.add_parser("adopt-revision", help="确认采用候选段落版本")
    adopt_parser.add_argument("deliverable_block_id")
    adopt_parser.add_argument("--version", type=int, required=True)
    export_parser = subparsers.add_parser("export", help="导出可编辑内部稿")
    export_parser.add_argument("project_id")
    export_parser.add_argument("exporter_key", nargs="?", default="markdown")
    export_parser.add_argument("--out", type=Path)
    walk_parser = subparsers.add_parser(
        "demo-walk",
        help="本机走完一题：建题、改稿、补材料、依据、过不过、导出 Word。不是网站。",
    )
    walk_parser.add_argument("--out", type=Path, required=True, help="写出口头记录和 Word 的目录")
    walk_parser.add_argument("--name", default="走查空白题")
    walk_parser.add_argument(
        "--plain",
        action="store_true",
        help="只印人话步骤，不印 JSON",
    )

    args = parser.parse_args()
    repository = SqliteRepository(args.db)
    repository.migrate()

    if args.command == "serve":
        try:
            serve_readonly_api(
                repository,
                host=args.host,
                port=args.port,
                open_url=args.open,
            )
        except OSError:
            raise SystemExit(
                "本机 8000 端口已被占用，而且占用它的不是经纬看稿进程。"
                "请先关掉占用 8000 的程序，再运行 scripts\\serve_readonly.ps1。"
            )
        return

    if args.command == "list-templates":
        # 建题目要填的是 template_key，而它写在 template.json 里，跟目录名不是一回事。
        # 没有这个入口的时候，命令行用户只能猜目录名，猜错只得到「未找到模板 xxx」。
        rows = [
            {
                "template_key": key,
                "name": template.name,
                "verification": template.verification,
                "verification_label": VERIFICATION_LEVELS.get(template.verification, template.verification),
                "listed": template.listed,
            }
            for key, template in sorted(load_templates().items())
            if args.all or template.listed
        ]
        print(json.dumps({"templates": rows}, ensure_ascii=False, indent=2))
        return

    if args.command == "migrate":
        result = {"database": str(args.db), "schema": SCHEMA_VERSION}
    elif args.command == "import-sample":
        result = {"project_id": import_sample(repository, args.sample)}
    elif args.command == "list-projects":
        result = build_project_list_projection(repository)
        if args.plain:
            projects = result.get("projects") or []
            if not projects:
                print("还没有题目。")
            else:
                print("这不是网站。已有题目：")
                for item in projects:
                    print("- " + (item.get("name") or item.get("id") or ""))
            return
    elif args.command == "create-project":
        result = create_project(
            repository,
            name=args.name,
            original_context=args.original_context,
            decision_question=args.decision_question,
            deliverable=args.deliverable,
            questions=args.questions,
            template_key=args.template,
        )
    elif args.command == "add-block":
        result = add_deliverable_block(
            repository,
            args.project_id,
            title=args.title,
            current_text=args.text,
            restriction=args.restriction,
        )
    elif args.command == "attach-claim":
        result = attach_claim_to_block(
            repository,
            args.deliverable_block_id,
            source_id=args.source,
            excerpt=args.excerpt,
            text=args.text,
            epistemic_type=args.epistemic_type,
            provenance_scope="client_provided" if args.client_provided else None,
            macro_market=args.macro,
            locator=args.locator,
            context_limit=args.context_limit,
        )
    elif args.command == "attach-finding":
        result = attach_finding_to_block(
            repository,
            args.deliverable_block_id,
            text=args.text,
            claim_ids=args.claim_ids,
            alternative=args.alternative,
            confidence=args.confidence,
        )
    elif args.command == "attach-option":
        result = attach_option_to_block(
            repository,
            args.deliverable_block_id,
            text=args.text,
            status=args.status,
        )
    elif args.command == "draft-suggestion":
        result = draft_model_suggestions(repository, args.deliverable_block_id)
    elif args.command == "draft-revision":
        result = draft_block_revision(repository, args.deliverable_block_id)
    elif args.command == "adopt-suggestion":
        result = adopt_model_suggestion(repository, args.suggestion_id)
    elif args.command == "dismiss-suggestion":
        result = dismiss_model_suggestion(repository, args.suggestion_id)
    elif args.command == "verify-claim":
        result = update_claim_verification(
            repository,
            args.deliverable_block_id,
            args.claim_id,
            verification_status=args.status,
        )
    elif args.command == "report":
        result = build_report_projection(repository, args.project_id)
    elif args.command == "brief":
        result = build_brief_projection(repository, args.project_id)
    elif args.command == "save-brief":
        result = update_brief(
            repository,
            args.project_id,
            original_context=args.original_context,
            decision_question=args.decision_question,
            deliverable=args.deliverable,
        )
    elif args.command == "capture-source":
        result = capture_local_source(
            repository,
            args.project_id,
            args.path,
            title=args.title,
            supersedes_source_id=args.supersedes,
        )
    elif args.command == "capture-candidate":
        result = capture_web_candidate(
            repository,
            args.project_id,
            url=args.url,
            title=args.title,
            note=args.note,
        )
    elif args.command == "open-candidate":
        result = open_web_candidate(repository, args.candidate_id)
    elif args.command == "promote-candidate":
        result = promote_web_candidate(
            repository,
            args.candidate_id,
            title=args.title,
        )
    elif args.command == "list-candidates":
        result = build_candidate_source_projection(repository, args.project_id)
    elif args.command == "search-materials":
        result = search_project_materials(
            repository,
            args.project_id,
            question_id=args.question_id,
        )
    elif args.command == "impact-preview":
        result = build_impact_preview(repository, args.source_id)
    elif args.command == "review-block":
        result = record_review_decision(
            repository,
            args.deliverable_block_id,
            action=args.action,
            reason=args.reason,
            proposed_text=args.proposed_text,
        )
    elif args.command == "override-block":
        result = record_override_decision(
            repository,
            deliverable_block_id=args.deliverable_block_id,
            handling=args.handling,
            reason=args.reason,
            review_trigger=args.review_trigger,
            proposed_text=args.proposed_text,
        )
    elif args.command == "override-project":
        result = record_override_decision(
            repository,
            project_id=args.project_id,
            handling=args.handling,
            reason=args.reason,
            review_trigger=args.review_trigger,
        )
    elif args.command == "propose-revision":
        result = propose_block_revision(
            repository,
            args.deliverable_block_id,
            body=args.text,
        )
    elif args.command == "adopt-revision":
        result = adopt_revision(
            repository, args.deliverable_block_id, args.version
        )
    elif args.command == "export":
        result = export_project(repository, args.project_id, args.exporter_key)
        if args.out:
            if result.get("content_encoding") == "base64":
                args.out.write_bytes(base64.b64decode(result["content"]))
            else:
                args.out.write_text(result["content"], encoding="utf-8")
            result = {
                "path": str(args.out),
                "filename": result["filename"],
                "block_ids": result["block_ids"],
            }
    elif args.command == "demo-walk":
        result = run_blank_walk(repository, args.out, name=args.name)
        if args.plain:
            print("这不是网站。经纬是本机研究工作流，交付是 Word。")
            for line in result["said"]:
                print(line)
            print("题目 " + result["project_id"])
            print("Word " + result["word_path"])
            return
    else:
        result = build_review_context(repository, args.deliverable_block_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
