from __future__ import annotations

from typing import Any

from app.adapters.sqlite_repository import SqliteRepository

QUESTION_STATUSES = {
    "not_started": "未开始",
    "in_progress": "研究中",
    "waiting_for_material": "待补料",
    "enough_for_now": "暂时够用",
    "challenged": "受质疑",
    "superseded": "已替代",
}
DECISION_GATES = {
    "brainstorm_ready": "可继续头脑风暴",
    "internal_review_ready": "可供内部评审",
    "client_ready": "可用于售前沟通",
}
_LIMITATION = (
    "只投影已保存的任务边界；已知、假设、未知尚未单独建表。"
    "本页从内部稿进入，不是画布或看板。"
)


def build_brief_projection(
    repository: SqliteRepository, project_id: str
) -> dict[str, Any]:
    """任务边界投影：Brief 与本轮 ResearchQuestion，不复制报告结论。"""
    with repository.connect() as connection:
        project = connection.execute(
            """
            SELECT id, name, decision_gate, schema_version, current_round
            FROM projects WHERE id = ?
            """,
            (project_id,),
        ).fetchone()
        if project is None:
            raise KeyError(f"项目 {project_id} 不存在")
        brief = connection.execute(
            """
            SELECT id, original_context, decision_question, deliverable,
                   not_a_final_client_recommendation
            FROM briefs WHERE project_id = ?
            """,
            (project_id,),
        ).fetchone()
        if brief is None:
            raise KeyError(f"项目 {project_id} 没有任务边界")
        questions = connection.execute(
            """
            SELECT id, question, enough_for_now, status, round_index, label,
                   target_block_id
            FROM research_questions
            WHERE project_id = ?
            ORDER BY rowid
            """,
            (project_id,),
        ).fetchall()

    gate = project["decision_gate"]
    current_round = int(project["current_round"] or 1)
    return {
        "project": {
            "id": project["id"],
            "name": project["name"],
            "decision_gate": gate,
            "decision_gate_label": DECISION_GATES.get(gate or "", gate),
            "current_round": current_round,
        },
        "current_round": current_round,
        "brief": {
            "id": brief["id"],
            "original_context": brief["original_context"],
            "decision_question": brief["decision_question"],
            "deliverable": brief["deliverable"],
            "not_a_final_client_recommendation": bool(
                brief["not_a_final_client_recommendation"]
            ),
        },
        "questions": [
            {
                "id": row["id"],
                "question": row["question"],
                "label": (row["label"] or "").strip() or None,
                # 这条问题的答案落在稿的哪一节。留空是合法的：看不出来就别硬塞。
                "target_block_id": row["target_block_id"],
                # 第一层只给名字：没有短名就按整句截断，不替人编一个。
                "short_label": _short_label(row["label"], row["question"]),
                "enough_for_now": row["enough_for_now"],
                "status": row["status"],
                "status_label": QUESTION_STATUSES.get(row["status"], row["status"]),
                "round_index": int(row["round_index"] or 1),
            }
            for row in questions
        ],
        "limitation": _LIMITATION,
    }


SHORT_LABEL_CHARS = 14


def _short_label(label: str | None, question: str | None) -> str:
    name = " ".join(str(label or "").split())
    if name:
        return name
    text = " ".join(str(question or "").split())
    if len(text) <= SHORT_LABEL_CHARS:
        return text
    return text[:SHORT_LABEL_CHARS].rstrip() + "…"
