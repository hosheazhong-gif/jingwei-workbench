from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.application.attach_claim import attach_claim_to_block
from app.application.capture_source import capture_local_source
from app.application.create_project import create_project
from app.application.export_deliverable import export_project
from app.application.review_block import (
    adopt_revision,
    propose_block_revision,
    record_review_decision,
)
from app.application.verify_claim import update_claim_verification

NOTE_NAME = "client-note.txt"
NOTE_BODY = "客户口头：本轮只要内部初稿，租户底表尚未提供。"
DRAFT_BODY = "本轮材料不足，只写缺口：租户底表未提供。客户口头不等于外部核实。"
CLAIM_TEXT = "客户口头表示本轮只要内部初稿，租户底表尚未提供。"


class DemoWalkError(ValueError):
    pass


def run_blank_walk(
    repository: SqliteRepository,
    output_dir: Path,
    *,
    name: str = "走查空白题",
) -> dict[str, Any]:
    """本机命令走完一题：建题、改稿、补材料、依据、看过原文、过不过、导出 Word。

    不覆盖已有题目，不代写，不把客户口头标成已核实。
    """
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    note_path = destination / NOTE_NAME
    note_path.write_text(NOTE_BODY, encoding="utf-8")

    created = create_project(
        repository,
        name=name,
        original_context="一句话：本轮能不能先交内部初稿。",
        decision_question="本轮是否只交缺口说明。",
        deliverable="内部研究初稿",
    )
    project_id = created["project_id"]
    blocks = created["report"]["blocks"]
    if not blocks:
        raise DemoWalkError("新建题目没有占位段落")
    block_id = blocks[0]["id"]

    proposed = propose_block_revision(repository, block_id, body=DRAFT_BODY)
    version = (proposed.get("pending_revision") or {}).get("version")
    if version is None:
        raise DemoWalkError("改稿没有得到版本号")
    adopt_revision(repository, block_id, version)

    captured = capture_local_source(
        repository,
        project_id,
        note_path,
        title="客户口头记录",
    )
    source_id = captured["source"]["id"]

    attached = attach_claim_to_block(
        repository,
        block_id,
        source_id=source_id,
        excerpt=NOTE_BODY,
        text=CLAIM_TEXT,
        epistemic_type="factual_claim",
        provenance_scope="client_provided",
    )
    claim_id = attached["claim_id"]

    update_claim_verification(
        repository,
        block_id,
        claim_id,
        verification_status="source_checked",
    )
    record_review_decision(repository, block_id, action="approve")

    exported = export_project(repository, project_id, "word")
    word_path = destination / (exported.get("filename") or "internal-draft.docx")
    word_path.write_bytes(base64.b64decode(exported["content"]))

    said = [
        "建了空白题目，没有代写。",
        "把占位稿换成缺口说明。",
        "保存了客户口头记录，没有解析成已核实事实。",
        "补了一句依据，并记下原文看过了。",
        "这段可以进本版。",
        "已导出 Word。这不是网站。",
    ]
    return {
        "project_id": project_id,
        "block_id": block_id,
        "source_id": source_id,
        "claim_id": claim_id,
        "word_path": str(word_path),
        "note_path": str(note_path),
        "said": said,
        "confirmation": {
            "recorded": True,
            "record_kind": "demo_walk",
            "verification_status_unchanged": False,
            "current_text_unchanged": False,
            "message": "本机命令已走完一题。客户口头仍不是外部核实。",
        },
    }
