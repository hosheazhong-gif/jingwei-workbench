from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository
from app.projections.brief import DECISION_GATES
from app.templates.registry import TemplateError, load_template

_LIMITATION = (
    "只列出已保存题目。打开后读取同一对象，不保存第二套结论。"
    "新建题目不会覆盖已有来源或内部稿。"
)


def build_project_list_projection(repository: SqliteRepository) -> dict[str, Any]:
    with repository.connect() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.name, p.template_key, p.stage, p.decision_gate,
                   p.schema_version, b.original_context, b.decision_question
            FROM projects p
            LEFT JOIN briefs b ON b.project_id = p.id
            ORDER BY p.rowid
            """
        ).fetchall()
    return {
        "projects": [
            {
                "id": row["id"],
                "name": row["name"],
                "template_key": row["template_key"],
                # 建完题目之后模板就再也不露面了，过两周回来只能靠题目名猜
                # 用的是哪套问法。把人话名字带上（2026-08-23 流水账）。
                "template_name": _template_name(row["template_key"]),
                "stage": row["stage"],
                "decision_gate": row["decision_gate"],
                "decision_gate_label": DECISION_GATES.get(
                    row["decision_gate"] or "", row["decision_gate"]
                ),
                "original_context": row["original_context"],
                "decision": row["decision_question"] or row["original_context"],
                "schema_version": row["schema_version"],
            }
            for row in rows
        ],
        "limitation": _LIMITATION,
    }


def _template_name(key: object) -> str:
    """模板的人话名字；模板文件被挪走时退回 key，不让界面空着。"""
    text = str(key or "").strip()
    if not text:
        return ""
    try:
        return load_template(text).name
    except TemplateError:
        return text
