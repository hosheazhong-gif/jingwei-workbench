"""按配置目录加载项目模板；不把业务词写进领域层。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_TEMPLATES_ROOT = Path(__file__).resolve().parent

# 没有显式选模板时用哪一份。放在这里而不是建题目那边：它是模板的事，
# 而且选模板那一栏也要知道谁是默认（否则会默认选中排序第一个）。
DEFAULT_TEMPLATE_KEY = "industry_chain_analysis_presales"


class TemplateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ConfiguredTemplate:
    key: str
    name: str
    execution_strategy_key: str
    brief_prompt: str | None
    # 只影响新建题目那一栏列不列它，不影响任何分析语义。接缝演练用的假模板
    # 显式写 false，真模板不写就是要列（默认 True），免得以后加模板忘了开。
    listed: bool
    # 纯界面文案：换掉它们的值，模型的输出不会变（PRD 20.10 的判断标准）。
    # 这份模板验到什么程度。**三档不是两档**——「问法在真题上试过」和
    # 「整条循环从头走到尾」是两件事，混成一个「已走查」就是在骗人：
    # 前者只证明这几条问法搜得出东西，后者才证明写稿和收下那几步也撑得住。
    # 使用者不是只有写这份配置的人，他分不出哪条问法有来处、哪条是拍脑袋。
    verification: str
    status_note: str
    # 七条问法各自从哪来。条数必须和 recommended_question_labels 对得上——
    # 主张要挂来源是这个工具的规矩，模板自己的问法没道理例外。
    provenance: tuple[str, ...]
    intro: str
    when_to_use: tuple[str, ...]
    when_not_to_use: tuple[str, ...]
    flow: tuple[dict, ...]
    steps: tuple[dict, ...]
    example: dict
    pitfalls: tuple[str, ...]
    _question_labels: tuple[str, ...]
    _labels: Mapping[str, str]

    def natural_language_labels(self) -> Mapping[str, str]:
        return dict(self._labels)

    def recommended_question_labels(self) -> Sequence[str]:
        return list(self._question_labels)


VERIFICATION_LEVELS = {
    # 整条循环在真题上从头走到尾过，**而且拟问题和写稿是真模型做的**。
    "loop_walked": "已在真题上走查过",
    # 七条问法拿真题逐条搜过；整条循环也用真公开材料走通了（挂材料、逐字扒
    # 原话、写稿、收下、核验、导出）。**唯独「模型按这个模板拟问题和写稿」
    # 那一步是人代做的**——差这一步就不许说「已走查」。
    "walked_by_hand": "真材料走通了，模型那一步还没验",
    # 只按公开做法拟了问法，一条都没试过。
    "skeleton": "只搭了骨架，还没试过",
}
_DEFAULT_VERIFICATION = "skeleton"


def _verification(data: dict) -> str:
    """认不出的值一律退回最低档。宁可说自己没验过，不要说验过了。"""
    value = str(data.get("verification") or "").strip()
    return value if value in VERIFICATION_LEVELS else _DEFAULT_VERIFICATION


def templates_root() -> Path:
    return _TEMPLATES_ROOT


def load_templates(root: Path | None = None) -> dict[str, ConfiguredTemplate]:
    base = root or _TEMPLATES_ROOT
    loaded: dict[str, ConfiguredTemplate] = {}
    if not base.is_dir():
        return loaded
    for path in sorted(base.glob("*/template.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        key = str(data["template_key"])
        loaded[key] = ConfiguredTemplate(
            key=key,
            name=str(data.get("name") or key),
            execution_strategy_key=str(data.get("execution_strategy_key") or "fixed_workflow"),
            brief_prompt=data.get("brief_prompt"),
            listed=bool(data.get("listed", True)),
            verification=_verification(data),
            status_note=str(data.get("status_note") or ""),
            provenance=tuple(str(x) for x in (data.get("provenance") or ())),
            intro=str(data.get("intro") or ""),
            when_to_use=tuple(str(x) for x in (data.get("when_to_use") or ())),
            when_not_to_use=tuple(str(x) for x in (data.get("when_not_to_use") or ())),
            flow=tuple(dict(x) for x in (data.get("flow") or ())),
            steps=tuple(dict(x) for x in (data.get("steps") or ())),
            example=dict(data.get("example") or {}),
            pitfalls=tuple(str(x) for x in (data.get("pitfalls") or ())),
            _question_labels=tuple(data.get("recommended_question_labels") or ()),
            _labels=dict(data.get("labels") or {}),
        )
    return loaded


def load_template(key: str, root: Path | None = None) -> ConfiguredTemplate:
    loaded = load_templates(root)
    template = loaded.get(key)
    if template is None:
        raise TemplateError(f"未找到模板 {key}")
    return template
