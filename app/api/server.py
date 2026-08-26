from __future__ import annotations

import json
import ipaddress
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import unquote, urlparse

from app.local_serve import is_address_in_use, recycle_jingwei_listeners
from app.adapters.local_source import MAX_UPLOAD_BYTES
from app.api.multipart import MultipartError, parse_multipart_form
from app.application.add_block import BlockWriteError, add_deliverable_block, remove_deliverable_block, rename_deliverable_block
from app.application.attach_claim import (
    ClaimAttachError,
    attach_claim_to_block,
    unlink_claim_from_block,
)
from app.application.attach_finding import FindingAttachError, attach_finding_to_block
from app.application.attach_option import OptionAttachError, attach_option_to_block
from app.application.verify_claim import ClaimVerifyError, update_claim_verification
from app.application.capture_source import (
    CaptureError,
    capture_local_source,
    capture_manager_feedback,
)
from app.application.candidate_source import (
    CandidateSourceError,
    capture_web_candidate,
    discard_web_candidate,
    restore_web_candidate,
    open_web_candidate,
    promote_web_candidate,
)
from app.application.search_materials import SearchMaterialsError, search_project_materials
from app.application.create_project import ProjectCreateError, create_project, ensure_review_shell
from app.application.export_folder import (
    EXPORT_LABELS,
    ExportFolderError,
    save_export_to_folder,
)
from app.application.remove_source import SourceRemoveError, remove_source
from app.application.delete_project import ProjectDeleteError, delete_project
from app.application.review_block import (
    ReviewError,
    adopt_revision,
    propose_block_revision,
    record_override_decision,
    record_review_decision,
)
from app.application.export_deliverable import ExportError, export_project
from app.application.update_brief import BriefUpdateError, update_brief
from app.application.question_progress import (
    QuestionProgressError,
    add_research_question,
    defer_research_question,
    restore_research_question,
    set_question_progress,
    set_question_target_block,
)
from app.application.round_questions import (
    RoundQuestionError,
    adopt_round_questions,
    draft_round_decision,
    draft_round_questions,
    rename_research_question,
)
from app.application.excerpt_from_snapshot import (
    ExcerptFromSnapshotError,
    adopt_snapshot_excerpts,
    draft_snapshot_excerpts,
)
from app.application.material_question import (
    MaterialQuestionError,
    assign_material_question,
    assign_materials_question,
)
from app.application.research_round import (
    ResearchRoundError,
    close_research_round,
    reopen_research_round,
)
from app.application.source_snapshot import SnapshotError, build_snapshot_view
from app.application.draft_suggestion import (
    DraftSuggestionError,
    adopt_model_suggestion,
    dismiss_model_suggestion,
    draft_block_revision,
    draft_model_suggestions,
)
from app.projections.brief import build_brief_projection
from app.projections.candidates import build_candidate_source_projection
from app.projections.impact import build_impact_preview
from app.projections.projects import build_project_list_projection
from app.projections.templates import build_template_list_projection
from app.projections.report import build_report_projection, build_review_context
from app.projections.sources import build_source_list_projection
from app.projections.workbench import build_workbench_projection
from app.local_env import load_local_env
from app.model_settings import (
    ModelSettingsError,
    model_settings_root,
    read_model_settings,
    save_model_settings,
)

_PROJECTS_PATH = re.compile(r"^/projects$")
_TEMPLATES_PATH = re.compile(r"^/templates$")
_MODEL_SETTINGS_PATH = re.compile(r"^/settings/model$")
_MODEL_SETTINGS_TEST_PATH = re.compile(r"^/settings/model/test$")
_PROJECT_ITEM_PATH = re.compile(r"^/projects/([^/]+)$")
_REPORT_PATH = re.compile(r"^/projects/([^/]+)/report$")
_WORKBENCH_PATH = re.compile(r"^/projects/([^/]+)/workbench$")
_BRIEF_PATH = re.compile(r"^/projects/([^/]+)/brief$")
_QUESTION_PROGRESS_PATH = re.compile(r"^/research-questions/([^/]+)/progress$")
_QUESTION_TARGET_PATH = re.compile(r"^/research-questions/([^/]+)/target-block$")
_PROJECT_QUESTIONS_PATH = re.compile(r"^/projects/([^/]+)/research-questions$")
_ROUND_QUESTION_DRAFT_PATH = re.compile(
    r"^/projects/([^/]+)/round-questions/draft$"
)
_ROUND_QUESTION_ADOPT_PATH = re.compile(
    r"^/projects/([^/]+)/round-questions/adopt$"
)
_QUESTION_DEFER_PATH = re.compile(r"^/research-questions/([^/]+)/defer$")
_QUESTION_RESTORE_PATH = re.compile(r"^/research-questions/([^/]+)/restore$")
_QUESTION_RENAME_PATH = re.compile(r"^/research-questions/([^/]+)/rename$")
_ROUND_CLOSE_PATH = re.compile(r"^/projects/([^/]+)/rounds/close$")
_ROUND_REOPEN_PATH = re.compile(r"^/projects/([^/]+)/rounds/reopen$")
_ROUND_DECISION_PATH = re.compile(r"^/projects/([^/]+)/round-decision/draft$")
_MANAGER_FEEDBACK_PATH = re.compile(r"^/projects/([^/]+)/manager-feedback$")
_SOURCE_SNAPSHOT_PATH = re.compile(r"^/sources/([^/]+)/snapshot$")
_SOURCE_EXCERPT_DRAFT_PATH = re.compile(r"^/sources/([^/]+)/excerpt-draft$")
_SOURCE_EXCERPT_ADOPT_PATH = re.compile(r"^/sources/([^/]+)/excerpt-draft/adopt$")
_SOURCE_QUESTION_PATH = re.compile(r"^/sources/([^/]+)/question$")
_SOURCE_ITEM_PATH = re.compile(r"^/sources/([^/]+)$")
_CANDIDATE_QUESTION_PATH = re.compile(r"^/candidate-sources/([^/]+)/question$")
_MATERIALS_QUESTION_PATH = re.compile(r"^/projects/([^/]+)/materials/question$")
_REVIEW_SHELL_PATH = re.compile(r"^/projects/([^/]+)/review-shell$")
_BLOCKS_PATH = re.compile(r"^/projects/([^/]+)/deliverable-blocks$")
_REVIEW_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/review-context$")
_CAPTURE_PATH = re.compile(r"^/projects/([^/]+)/sources$")
_CANDIDATES_PATH = re.compile(r"^/projects/([^/]+)/candidate-sources$")
_MATERIAL_SEARCH_PATH = re.compile(r"^/projects/([^/]+)/material-search$")
_CANDIDATE_OPEN_PATH = re.compile(r"^/candidate-sources/([^/]+)/open$")
_CANDIDATE_PROMOTE_PATH = re.compile(r"^/candidate-sources/([^/]+)/promote$")
_CANDIDATE_DISCARD_PATH = re.compile(r"^/candidate-sources/([^/]+)/discard$")
_CANDIDATE_RESTORE_PATH = re.compile(r"^/candidate-sources/([^/]+)/restore$")
_BLOCK_ITEM_PATH = re.compile(r"^/deliverable-blocks/([^/]+)$")
_BLOCK_TITLE_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/title$")
_IMPACT_PATH = re.compile(r"^/sources/([^/]+)/impact-preview$")
_CLAIM_ATTACH_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/claims$")
_CLAIM_UNLINK_PATH = re.compile(
    r"^/deliverable-blocks/([^/]+)/claims/([^/]+)/unlink$"
)
_CLAIM_VERIFY_PATH = re.compile(
    r"^/deliverable-blocks/([^/]+)/claims/([^/]+)/verification$"
)
_FINDING_ATTACH_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/findings$")
_OPTION_ATTACH_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/options$")
_SUGGESTION_DRAFT_PATH = re.compile(
    r"^/deliverable-blocks/([^/]+)/model-suggestions$"
)
_DRAFT_REVISION_PATH = re.compile(
    r"^/deliverable-blocks/([^/]+)/draft-revision$"
)
_SUGGESTION_ADOPT_PATH = re.compile(r"^/model-suggestions/([^/]+)/adopt$")
_SUGGESTION_DISMISS_PATH = re.compile(r"^/model-suggestions/([^/]+)/dismiss$")
_REVIEW_WRITE_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/review-decisions$")
_BLOCK_OVERRIDE_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/override-decisions$")
_PROJECT_OVERRIDE_PATH = re.compile(r"^/projects/([^/]+)/override-decisions$")
_ADOPT_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/revisions/adopt$")
_REVISIONS_PATH = re.compile(r"^/deliverable-blocks/([^/]+)/revisions$")
_EXPORT_PATH = re.compile(r"^/projects/([^/]+)/exports/([^/]+)$")
_FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "report"
_STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/app.js": "app.js",
    "/styles.css": "styles.css",
}
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
}
MAX_POST_BODY = MAX_UPLOAD_BYTES + 256 * 1024


def _is_loopback_host(host: str | None) -> bool:
    """本机工作台不得静默暴露到局域网或公网。"""
    value = str(host or "").strip().lower().strip("[]")
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
        return address.version == 4 and address.is_loopback
    except ValueError:
        return False


def _optional_draft_adapter():
    from app.adapters.http_draft import resolve_draft_adapter
    from app.adapters.unconfigured_draft import UnconfiguredDraftAdapter

    resolved = resolve_draft_adapter()
    if isinstance(resolved, UnconfiguredDraftAdapter):
        return None
    return resolved


def dispatch_get(repository: SqliteRepository, path: str) -> tuple[int, dict[str, Any]]:
    """将只读路径映射到现有投影；不复制业务状态。"""
    if _PROJECTS_PATH.fullmatch(path):
        return 200, build_project_list_projection(repository)

    if _TEMPLATES_PATH.fullmatch(path):
        return 200, build_template_list_projection()

    if _MODEL_SETTINGS_PATH.fullmatch(path):
        try:
            return 200, read_model_settings()
        except ModelSettingsError as error:
            return 400, {"error": str(error)}

    report_match = _REPORT_PATH.fullmatch(path)
    if report_match:
        project_id = unquote(report_match.group(1))
        try:
            return 200, build_report_projection(repository, project_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    workbench_match = _WORKBENCH_PATH.fullmatch(path)
    if workbench_match:
        project_id = unquote(workbench_match.group(1))
        try:
            return 200, build_workbench_projection(repository, project_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    review_match = _REVIEW_PATH.fullmatch(path)
    if review_match:
        block_id = unquote(review_match.group(1))
        try:
            return 200, build_review_context(repository, block_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    impact_match = _IMPACT_PATH.fullmatch(path)
    if impact_match:
        source_id = unquote(impact_match.group(1))
        try:
            return 200, build_impact_preview(repository, source_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    brief_match = _BRIEF_PATH.fullmatch(path)
    if brief_match:
        project_id = unquote(brief_match.group(1))
        try:
            return 200, build_brief_projection(repository, project_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    sources_match = _CAPTURE_PATH.fullmatch(path)
    if sources_match:
        project_id = unquote(sources_match.group(1))
        try:
            return 200, build_source_list_projection(repository, project_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    candidates_match = _CANDIDATES_PATH.fullmatch(path)
    if candidates_match:
        project_id = unquote(candidates_match.group(1))
        try:
            return 200, build_candidate_source_projection(repository, project_id)
        except KeyError as error:
            return 404, {"error": str(error)}

    return 404, {"error": "未找到该接口"}


def dispatch_post(
    repository: SqliteRepository, path: str, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    if _MODEL_SETTINGS_PATH.fullmatch(path):
        try:
            return 200, save_model_settings(
                model_settings_root(repository), payload
            )
        except ModelSettingsError as error:
            return 400, {"error": str(error)}
    if _MODEL_SETTINGS_TEST_PATH.fullmatch(path):
        from app.adapters.http_draft import test_draft_connection

        try:
            return 200, test_draft_connection()
        except DraftSuggestionError as error:
            return 400, {"error": str(error)}
    if _PROJECTS_PATH.fullmatch(path):
        try:
            result = create_project(
                repository,
                name=payload.get("name"),
                original_context=payload.get("original_context"),
                decision_question=payload.get("decision_question"),
                deliverable=payload.get("deliverable"),
                not_a_final_client_recommendation=bool(
                    payload.get("not_a_final_client_recommendation", True)
                ),
                questions=payload.get("questions"),
                template_key=payload.get("template_key"),
            )
        except ProjectCreateError as error:
            return 400, {"error": str(error)}
        return 201, result
    shell_match = _REVIEW_SHELL_PATH.fullmatch(path)
    if shell_match:
        try:
            result = ensure_review_shell(
                repository, unquote(shell_match.group(1))
            )
        except ProjectCreateError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201 if result["created"] else 200, result
    blocks_match = _BLOCKS_PATH.fullmatch(path)
    if blocks_match:
        try:
            result = add_deliverable_block(
                repository,
                unquote(blocks_match.group(1)),
                title=payload.get("title"),
                current_text=payload.get("current_text"),
                restriction=payload.get("restriction"),
            )
        except BlockWriteError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    title_match = _BLOCK_TITLE_PATH.fullmatch(path)
    if title_match:
        try:
            result = rename_deliverable_block(
                repository,
                unquote(title_match.group(1)),
                title=payload.get("title"),
            )
        except BlockWriteError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    capture_match = _CAPTURE_PATH.fullmatch(path)
    if capture_match:
        project_id = unquote(capture_match.group(1))
        source_path = payload.get("path")
        if not source_path:
            return 400, {"error": "缺少 path"}
        return _capture_source(
            repository,
            project_id,
            source_path=source_path,
            title=payload.get("title"),
            supersedes_source_id=payload.get("supersedes_source_id"),
            question_id=payload.get("question_id")
            or payload.get("research_question_id"),
        )
    candidates_match = _CANDIDATES_PATH.fullmatch(path)
    if candidates_match:
        try:
            result = capture_web_candidate(
                repository,
                unquote(candidates_match.group(1)),
                url=payload.get("url"),
                title=payload.get("title"),
                note=payload.get("note"),
                question_id=payload.get("question_id")
                or payload.get("research_question_id"),
            )
        except CandidateSourceError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    search_match = _MATERIAL_SEARCH_PATH.fullmatch(path)
    if search_match:
        try:
            result = search_project_materials(
                repository,
                unquote(search_match.group(1)),
                question_id=payload.get("question_id"),
                draft_adapter=_optional_draft_adapter(),
            )
        except SearchMaterialsError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    open_match = _CANDIDATE_OPEN_PATH.fullmatch(path)
    if open_match:
        try:
            result = open_web_candidate(repository, unquote(open_match.group(1)))
        except CandidateSourceError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    promote_match = _CANDIDATE_PROMOTE_PATH.fullmatch(path)
    if promote_match:
        try:
            result = promote_web_candidate(
                repository,
                unquote(promote_match.group(1)),
                title=payload.get("title"),
            )
        except CandidateSourceError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    discard_match = _CANDIDATE_DISCARD_PATH.fullmatch(path)
    if discard_match:
        try:
            result = discard_web_candidate(
                repository, unquote(discard_match.group(1))
            )
        except CandidateSourceError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    restore_match = _CANDIDATE_RESTORE_PATH.fullmatch(path)
    if restore_match:
        try:
            result = restore_web_candidate(
                repository, unquote(restore_match.group(1))
            )
        except CandidateSourceError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    claim_match = _CLAIM_ATTACH_PATH.fullmatch(path)
    if claim_match:
        try:
            result = attach_claim_to_block(
                repository,
                unquote(claim_match.group(1)),
                source_id=payload.get("source_id"),
                excerpt=payload.get("excerpt"),
                text=payload.get("text"),
                epistemic_type=payload.get("epistemic_type"),
                provenance_scope=payload.get("provenance_scope"),
                macro_market=bool(payload.get("macro_market")),
                locator=payload.get("locator"),
                context_limit=payload.get("context_limit"),
                locator_kind=payload.get("locator_kind"),
            )
        except ClaimAttachError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    unlink_match = _CLAIM_UNLINK_PATH.fullmatch(path)
    if unlink_match:
        try:
            result = unlink_claim_from_block(
                repository,
                unquote(unlink_match.group(1)),
                unquote(unlink_match.group(2)),
            )
        except ClaimAttachError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    verify_match = _CLAIM_VERIFY_PATH.fullmatch(path)
    if verify_match:
        try:
            result = update_claim_verification(
                repository,
                unquote(verify_match.group(1)),
                unquote(verify_match.group(2)),
                verification_status=payload.get("verification_status"),
            )
        except ClaimVerifyError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    finding_match = _FINDING_ATTACH_PATH.fullmatch(path)
    if finding_match:
        try:
            result = attach_finding_to_block(
                repository,
                unquote(finding_match.group(1)),
                text=payload.get("text"),
                claim_ids=payload.get("claim_ids"),
                alternative=payload.get("alternative"),
                confidence=payload.get("confidence"),
            )
        except FindingAttachError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    option_match = _OPTION_ATTACH_PATH.fullmatch(path)
    if option_match:
        try:
            result = attach_option_to_block(
                repository,
                unquote(option_match.group(1)),
                text=payload.get("text"),
                status=payload.get("status"),
            )
        except OptionAttachError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    suggestion_draft = _SUGGESTION_DRAFT_PATH.fullmatch(path)
    if suggestion_draft:
        try:
            result = draft_model_suggestions(
                repository,
                unquote(suggestion_draft.group(1)),
            )
        except DraftSuggestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    draft_revision = _DRAFT_REVISION_PATH.fullmatch(path)
    if draft_revision:
        try:
            result = draft_block_revision(
                repository,
                unquote(draft_revision.group(1)),
                question_id=payload.get("question_id"),
            )
        except DraftSuggestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    suggestion_adopt = _SUGGESTION_ADOPT_PATH.fullmatch(path)
    if suggestion_adopt:
        try:
            result = adopt_model_suggestion(
                repository, unquote(suggestion_adopt.group(1))
            )
        except DraftSuggestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    suggestion_dismiss = _SUGGESTION_DISMISS_PATH.fullmatch(path)
    if suggestion_dismiss:
        try:
            result = dismiss_model_suggestion(
                repository, unquote(suggestion_dismiss.group(1))
            )
        except DraftSuggestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    review_write = _REVIEW_WRITE_PATH.fullmatch(path)
    if review_write:
        try:
            result = record_review_decision(
                repository,
                unquote(review_write.group(1)),
                action=str(payload.get("action") or ""),
                reason=payload.get("reason"),
                proposed_text=payload.get("proposed_text"),
                actor=str(payload.get("actor") or "analyst"),
            )
        except ReviewError as error:
            return _review_status(error), {"error": str(error)}
        return 201, result
    block_override = _BLOCK_OVERRIDE_PATH.fullmatch(path)
    if block_override:
        try:
            result = record_override_decision(
                repository,
                deliverable_block_id=unquote(block_override.group(1)),
                handling=str(payload.get("handling") or ""),
                reason=payload.get("reason"),
                review_trigger=payload.get("review_trigger"),
                proposed_text=payload.get("proposed_text"),
            )
        except ReviewError as error:
            return _review_status(error), {"error": str(error)}
        return 201, result
    project_override = _PROJECT_OVERRIDE_PATH.fullmatch(path)
    if project_override:
        try:
            result = record_override_decision(
                repository,
                project_id=unquote(project_override.group(1)),
                handling=str(payload.get("handling") or ""),
                reason=payload.get("reason"),
                review_trigger=payload.get("review_trigger"),
            )
        except ReviewError as error:
            return _review_status(error), {"error": str(error)}
        return 201, result
    propose_match = _REVISIONS_PATH.fullmatch(path)
    if propose_match:
        try:
            result = propose_block_revision(
                repository,
                unquote(propose_match.group(1)),
                body=payload.get("body"),
            )
        except ReviewError as error:
            return _review_status(error), {"error": str(error)}
        return 201, result
    adopt_match = _ADOPT_PATH.fullmatch(path)
    if adopt_match:
        version = payload.get("version")
        try:
            version_number = int(version)
        except (TypeError, ValueError):
            return 400, {"error": "缺少候选版本号"}
        try:
            result = adopt_revision(
                repository,
                unquote(adopt_match.group(1)),
                version_number,
            )
        except ReviewError as error:
            return _review_status(error), {"error": str(error)}
        return 200, result
    export_match = _EXPORT_PATH.fullmatch(path)
    if export_match:
        project_id = unquote(export_match.group(1))
        try:
            result = export_project(
                repository,
                project_id,
                unquote(export_match.group(2)),
            )
        except ExportError as error:
            message = str(error)
            status = 404 if ("不存在" in message or "未找到" in message) else 400
            return status, {"error": message}
        if payload.get("save_to_folder"):
            # 存到题目文件夹是一次文件操作，不进账本；存不下也不该把
            # 已经导出的内容吞掉，只把原因说出来（PRD 20.9）。
            try:
                saved = _save_export(repository, project_id, result)
                result["saved_path"] = str(saved)
                result["confirmation"]["saved_path"] = str(saved)
                result["confirmation"]["message"] += f"　已存到「{saved.parent.name}」文件夹。"
            except (ExportFolderError, OSError) as error:
                result["save_error"] = str(error)
                result["confirmation"]["message"] += f"　没能存到题目文件夹：{error}"
        return 200, result
    brief_match = _BRIEF_PATH.fullmatch(path)
    if brief_match:
        try:
            result = update_brief(
                repository,
                unquote(brief_match.group(1)),
                original_context=payload.get("original_context"),
                decision_question=payload.get("decision_question"),
                deliverable=payload.get("deliverable"),
                name=payload.get("name"),
                not_a_final_client_recommendation=payload.get(
                    "not_a_final_client_recommendation"
                ),
                questions=payload.get("questions"),
            )
        except BriefUpdateError as error:
            message = str(error)
            status = 404 if "不存在" in message or "没有任务边界" in message else 400
            return status, {"error": message}
        return 200, result
    progress_match = _QUESTION_PROGRESS_PATH.fullmatch(path)
    if progress_match:
        try:
            result = set_question_progress(
                repository,
                unquote(progress_match.group(1)),
                payload.get("progress"),
            )
        except QuestionProgressError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    target_match = _QUESTION_TARGET_PATH.fullmatch(path)
    if target_match:
        try:
            result = set_question_target_block(
                repository,
                unquote(target_match.group(1)),
                payload.get("target_block_id"),
            )
        except QuestionProgressError as error:
            message = str(error)
            status = 404 if "不存在" in message or "没有这一节" in message else 400
            return status, {"error": message}
        return 200, result
    add_question_match = _PROJECT_QUESTIONS_PATH.fullmatch(path)
    if add_question_match:
        try:
            result = add_research_question(
                repository,
                unquote(add_question_match.group(1)),
                question=payload.get("question"),
                enough_for_now=payload.get("enough_for_now"),
            )
        except QuestionProgressError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 201, result
    draft_questions_match = _ROUND_QUESTION_DRAFT_PATH.fullmatch(path)
    if draft_questions_match:
        try:
            result = draft_round_questions(
                repository,
                unquote(draft_questions_match.group(1)),
            )
        except RoundQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    adopt_questions_match = _ROUND_QUESTION_ADOPT_PATH.fullmatch(path)
    if adopt_questions_match:
        try:
            result = adopt_round_questions(
                repository,
                unquote(adopt_questions_match.group(1)),
                payload.get("questions"),
            )
        except RoundQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    close_round_match = _ROUND_CLOSE_PATH.fullmatch(path)
    if close_round_match:
        try:
            result = close_research_round(
                repository,
                unquote(close_round_match.group(1)),
            )
        except ResearchRoundError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    defer_match = _QUESTION_DEFER_PATH.fullmatch(path)
    if defer_match:
        try:
            result = defer_research_question(
                repository, unquote(defer_match.group(1))
            )
        except QuestionProgressError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    restore_match = _QUESTION_RESTORE_PATH.fullmatch(path)
    if restore_match:
        try:
            result = restore_research_question(
                repository, unquote(restore_match.group(1))
            )
        except QuestionProgressError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    rename_match = _QUESTION_RENAME_PATH.fullmatch(path)
    if rename_match:
        try:
            result = rename_research_question(
                repository,
                unquote(rename_match.group(1)),
                payload.get("question"),
                label=payload.get("label"),
            )
        except RoundQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    excerpt_adopt = _SOURCE_EXCERPT_ADOPT_PATH.fullmatch(path)
    if excerpt_adopt:
        try:
            result = adopt_snapshot_excerpts(
                repository,
                unquote(excerpt_adopt.group(1)),
                deliverable_block_id=payload.get("deliverable_block_id")
                or payload.get("block_id"),
                excerpts=payload.get("excerpts"),
            )
        except ExcerptFromSnapshotError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    excerpt_draft = _SOURCE_EXCERPT_DRAFT_PATH.fullmatch(path)
    if excerpt_draft:
        try:
            result = draft_snapshot_excerpts(
                repository,
                unquote(excerpt_draft.group(1)),
                question_id=payload.get("question_id"),
                deliverable_block_id=payload.get("deliverable_block_id")
                or payload.get("block_id"),
            )
        except ExcerptFromSnapshotError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    reopen_match = _ROUND_REOPEN_PATH.fullmatch(path)
    if reopen_match:
        try:
            result = reopen_research_round(repository, unquote(reopen_match.group(1)))
        except ResearchRoundError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    decision_match = _ROUND_DECISION_PATH.fullmatch(path)
    if decision_match:
        try:
            result = draft_round_decision(repository, unquote(decision_match.group(1)))
        except RoundQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    feedback_match = _MANAGER_FEEDBACK_PATH.fullmatch(path)
    if feedback_match:
        try:
            result = capture_manager_feedback(
                repository,
                unquote(feedback_match.group(1)),
                text=payload.get("text"),
                title=payload.get("title"),
            )
        except CaptureError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    materials_question = _MATERIALS_QUESTION_PATH.fullmatch(path)
    if materials_question:
        try:
            result = assign_materials_question(
                repository,
                unquote(materials_question.group(1)),
                source_ids=payload.get("source_ids"),
                candidate_ids=payload.get("candidate_ids"),
                question_id=payload.get("question_id"),
            )
        except MaterialQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    source_question = _SOURCE_QUESTION_PATH.fullmatch(path)
    if source_question:
        try:
            result = assign_material_question(
                repository,
                source_id=unquote(source_question.group(1)),
                question_id=payload.get("question_id"),
            )
        except MaterialQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    candidate_question = _CANDIDATE_QUESTION_PATH.fullmatch(path)
    if candidate_question:
        try:
            result = assign_material_question(
                repository,
                candidate_id=unquote(candidate_question.group(1)),
                question_id=payload.get("question_id"),
            )
        except MaterialQuestionError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    if is_readonly_route(path):
        return 405, {"error": "只读 API 不接受写入"}
    return 404, {"error": "未找到该接口"}



def _save_export(
    repository: SqliteRepository, project_id: str, export: dict[str, Any]
) -> Path:
    """把导出顺手存一份到题目文件夹。受控副本不动（PRD 20.9）。"""
    from datetime import UTC, datetime

    with repository.connect() as connection:
        row = connection.execute(
            "SELECT name FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
    name = row["name"] if row is not None else project_id
    configured_root = str(os.environ.get("JINGWEI_EXPORT_DIR") or "").strip()
    exports_root = (
        Path(configured_root).expanduser()
        if configured_root
        else Path(__file__).resolve().parents[2] / "导出"
    )
    return save_export_to_folder(
        export,
        exports_root=exports_root,
        project_name=name,
        project_id=project_id,
        stamp=datetime.now(UTC).astimezone().strftime("%Y-%m-%d"),
        label=EXPORT_LABELS.get(str(export.get("exporter_key") or ""), ""),
    )


def dispatch_delete(
    repository: SqliteRepository, path: str
) -> tuple[int, dict[str, Any]]:
    block_match = _BLOCK_ITEM_PATH.fullmatch(path)
    if block_match:
        try:
            result = remove_deliverable_block(
                repository, unquote(block_match.group(1))
            )
        except BlockWriteError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    source_match = _SOURCE_ITEM_PATH.fullmatch(path)
    if source_match:
        try:
            result = remove_source(repository, unquote(source_match.group(1)))
        except SourceRemoveError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    item_match = _PROJECT_ITEM_PATH.fullmatch(path)
    if item_match:
        try:
            result = delete_project(repository, unquote(item_match.group(1)))
        except ProjectDeleteError as error:
            message = str(error)
            status = 404 if "不存在" in message else 400
            return status, {"error": message}
        return 200, result
    if is_readonly_route(path):
        return 405, {"error": "只读 API 不接受写入"}
    return 404, {"error": "未找到该接口"}


def _capture_source(
    repository: SqliteRepository,
    project_id: str,
    *,
    source_path: str | None = None,
    title: str | None = None,
    supersedes_source_id: str | None = None,
    uploaded_name: str | None = None,
    uploaded_bytes: bytes | None = None,
    question_id: str | None = None,
) -> tuple[int, dict[str, Any]]:
    try:
        result = capture_local_source(
            repository,
            project_id,
            source_path,
            title=title,
            supersedes_source_id=supersedes_source_id,
            uploaded_name=uploaded_name,
            uploaded_bytes=uploaded_bytes,
            question_id=question_id,
        )
    except CaptureError as error:
        message = str(error)
        status = 404 if "不存在" in message else 400
        return status, {"error": message}
    return 201, result


def dispatch_multipart_post(
    repository: SqliteRepository, path: str, content_type: str, body: bytes
) -> tuple[int, dict[str, Any]]:
    capture_match = _CAPTURE_PATH.fullmatch(path)
    if not capture_match:
        return 400, {"error": "该接口不接受文件上传"}
    try:
        fields, files = parse_multipart_form(content_type, body)
    except MultipartError as error:
        return 400, {"error": str(error)}
    upload = files.get("file")
    if not upload:
        return 400, {"error": "缺少文件"}
    filename, data = upload
    title = (fields.get("title") or "").strip() or None
    supersedes = (fields.get("supersedes_source_id") or "").strip() or None
    question_id = (
        fields.get("question_id") or fields.get("research_question_id") or ""
    ).strip() or None
    return _capture_source(
        repository,
        unquote(capture_match.group(1)),
        title=title,
        supersedes_source_id=supersedes,
        uploaded_name=filename,
        uploaded_bytes=data,
        question_id=question_id,
    )


def is_readonly_route(path: str) -> bool:
    return bool(
        _MODEL_SETTINGS_PATH.fullmatch(path)
        or _REPORT_PATH.fullmatch(path)
        or _WORKBENCH_PATH.fullmatch(path)
        or _REVIEW_PATH.fullmatch(path)
        or _IMPACT_PATH.fullmatch(path)
        or _SOURCE_SNAPSHOT_PATH.fullmatch(path)
    )


def _review_status(error: ReviewError) -> int:
    return 404 if "不存在" in str(error) else 400


def resolve_static_file(path: str) -> Path | None:
    name = _STATIC_FILES.get(path)
    if name is None:
        return None
    frontend_root = _FRONTEND_DIR.resolve()
    target = (frontend_root / name).resolve()
    try:
        target.relative_to(frontend_root)
    except ValueError:
        return None
    if not target.is_file():
        return None
    return target


class ReadOnlyApiHandler(BaseHTTPRequestHandler):
    repository: SqliteRepository

    def do_GET(self) -> None:
        path = unquote(urlparse(self.path).path)
        static_file = resolve_static_file(path)
        if static_file is not None:
            self._write_file(static_file)
            return
        snapshot_match = _SOURCE_SNAPSHOT_PATH.fullmatch(path)
        if snapshot_match:
            try:
                body, content_type = build_snapshot_view(
                    self.repository, unquote(snapshot_match.group(1))
                )
            except SnapshotError as error:
                status = 404 if "不存在" in str(error) else 400
                self._write_json(status, {"error": str(error)})
                return
            sandbox = not content_type.startswith("text/")
            self._write_bytes(body, content_type, sandbox=sandbox)
            return
        status, payload = dispatch_get(self.repository, path)
        self._write_json(status, payload)

    def do_POST(self) -> None:
        path = unquote(urlparse(self.path).path)
        length = int(self.headers.get("Content-Length") or "0")
        if length > MAX_POST_BODY:
            self._drain_body(length)
            self._write_json(413, {"error": "文件过大，请不超过 20MB"})
            return
        if not self._allows_browser_write():
            self._drain_body(length)
            self._write_json(403, {"error": "只接受来自当前本机工作台的写入请求"})
            return
        raw = self._read_body()
        content_type = self.headers.get("Content-Type") or ""
        if "multipart/form-data" in content_type.lower():
            status, payload = dispatch_multipart_post(
                self.repository, path, content_type, raw
            )
            self._write_json(status, payload)
            return
        if not raw:
            status, payload = dispatch_post(self.repository, path, {})
            self._write_json(status, payload)
            return
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._write_json(400, {"error": "请求体必须是 JSON"})
            return
        if not isinstance(parsed, dict):
            self._write_json(400, {"error": "请求体必须是 JSON 对象"})
            return
        status, payload = dispatch_post(self.repository, path, parsed)
        self._write_json(status, payload)

    def do_PUT(self) -> None:
        self._reject_write()

    def do_PATCH(self) -> None:
        self._reject_write()

    def do_DELETE(self) -> None:
        if not self._allows_browser_write():
            self._write_json(403, {"error": "只接受来自当前本机工作台的写入请求"})
            return
        path = unquote(urlparse(self.path).path)
        status, payload = dispatch_delete(self.repository, path)
        self._write_json(status, payload)

    def _allows_browser_write(self) -> bool:
        """挡住别的网页借浏览器向本机工作台发写请求。

        CLI 和测试客户端通常不带 Origin，继续允许；浏览器带 Origin 时必须是
        同一端口上的回环地址。Sec-Fetch-Site 明说 cross-site 时直接拒绝。
        """
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"} or not _is_loopback_host(parsed.hostname):
            return False
        try:
            origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            return False
        return origin_port == int(self.server.server_address[1])

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _drain_body(self, length: int) -> None:
        remaining = max(length, 0)
        while remaining > 0:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

    def _reject_write(self) -> None:
        path = unquote(urlparse(self.path).path)
        if is_readonly_route(path):
            self._write_json(405, {"error": "只读 API 不接受写入"})
            return
        self._write_json(404, {"error": "未找到该接口"})

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _write_file(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = _STATIC_TYPES.get(path.suffix, "application/octet-stream")
        self._write_bytes(body, content_type)

    def _write_bytes(
        self, body: bytes, content_type: str, *, sandbox: bool = False
    ) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if sandbox:
            self.send_header("Content-Security-Policy", "sandbox")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


class ReadOnlyHttpServer:
    def __init__(
        self,
        repository: SqliteRepository,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if not _is_loopback_host(host):
            raise ValueError(
                "经纬是本机工作台，只能监听 IPv4 回环地址或 localhost。"
            )
        load_local_env(model_settings_root(repository))
        handler = type(
            "BoundReadOnlyApiHandler",
            (ReadOnlyApiHandler,),
            {"repository": repository},
        )

        class BoundServer(ThreadingHTTPServer):
            allow_reuse_address = False

        self._httpd = BoundServer((host, port), handler)
        self._thread = Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1])

    @property
    def origin(self) -> str:
        host, port = self._httpd.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._thread.join(timeout=5)
        self._httpd.server_close()


def serve_readonly_api(
    repository: SqliteRepository,
    host: str = "127.0.0.1",
    port: int = 8000,
    open_url: str | None = None,
) -> None:
    recycled = recycle_jingwei_listeners(port)
    if recycled:
        print("已关掉旧的看稿进程，正在重新打开。", flush=True)
    server = None
    last_error: OSError | None = None
    for _attempt in range(6):
        try:
            server = ReadOnlyHttpServer(repository, host=host, port=port)
            break
        except OSError as error:
            last_error = error
            if not is_address_in_use(error):
                raise
            time.sleep(0.5)
    if server is None:
        raise last_error if last_error is not None else OSError("无法打开看稿端口")
    server.start()
    if open_url:
        import webbrowser

        webbrowser.open(open_url)
    print(
        json.dumps(
            {
                "origin": server.origin,
                "ui": f"{server.origin}/",
                "routes": [
                    "GET /",
                    "GET /projects",
                    "POST /projects",
                    "POST /projects/{id}/review-shell",
                    "POST /projects/{id}/deliverable-blocks",
                    "POST /deliverable-blocks/{id}/title",
                    "DELETE /projects/{id}",
                    "GET /projects/{id}/report",
                    "GET /projects/{id}/workbench",
                    "GET /templates",
                    "GET /settings/model",
                    "POST /settings/model",
                    "POST /settings/model/test",
                    "POST /projects/{id}/brief",
                    "POST /research-questions/{id}/progress",
                    "POST /research-questions/{id}/target-block",
                    "POST /projects/{id}/research-questions",
                    "POST /projects/{id}/round-questions/draft",
                    "POST /projects/{id}/round-questions/adopt",
                    "POST /projects/{id}/rounds/close",
                    "POST /projects/{id}/rounds/reopen",
                    "POST /projects/{id}/round-decision/draft",
                    "POST /projects/{id}/manager-feedback",
                    "POST /research-questions/{id}/defer",
                    "POST /research-questions/{id}/restore",
                    "POST /research-questions/{id}/rename",
                    "GET /deliverable-blocks/{id}/review-context",
                    "POST /deliverable-blocks/{id}/claims",
                    "POST /deliverable-blocks/{id}/claims/{claim_id}/unlink",
                    "POST /deliverable-blocks/{id}/claims/{claim_id}/verification",
                    "POST /deliverable-blocks/{id}/findings",
                    "POST /deliverable-blocks/{id}/options",
                    "POST /deliverable-blocks/{id}/model-suggestions",
                    "POST /deliverable-blocks/{id}/draft-revision",
                    "POST /model-suggestions/{id}/adopt",
                    "POST /model-suggestions/{id}/dismiss",
                    "POST /deliverable-blocks/{id}/revisions",
                    "GET /sources/{id}/impact-preview",
                    "DELETE /sources/{id}",
                    "GET /sources/{id}/snapshot",
                    "POST /sources/{id}/excerpt-draft",
                    "POST /sources/{id}/excerpt-draft/adopt",
                    "POST /sources/{id}/question",
                    "POST /candidate-sources/{id}/question",
                    "POST /projects/{id}/materials/question",
                    "GET /projects/{id}/sources",
                    "POST /projects/{id}/sources",
                    "GET /projects/{id}/candidate-sources",
                    "POST /projects/{id}/candidate-sources",
                    "POST /projects/{id}/material-search",
                    "POST /candidate-sources/{id}/open",
                    "POST /candidate-sources/{id}/promote",
                    "POST /candidate-sources/{id}/discard",
                    "POST /candidate-sources/{id}/restore",
                    "DELETE /deliverable-blocks/{id}",
                    "POST /deliverable-blocks/{id}/review-decisions",
                    "POST /deliverable-blocks/{id}/override-decisions",
                    "POST /projects/{id}/override-decisions",
                    "POST /deliverable-blocks/{id}/revisions/adopt",
                    "POST /projects/{id}/exports/{exporter_key}",
                ],
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server._thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
