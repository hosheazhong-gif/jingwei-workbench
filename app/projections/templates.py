from __future__ import annotations

from typing import Any

from app.templates.registry import (
    DEFAULT_TEMPLATE_KEY,
    VERIFICATION_LEVELS,
    load_templates,
)

_LIMITATION = (
    "只列出可选模板。模板给的是拆本轮问题时的参考提示和界面用词，"
    "不写入问题、不写稿、不改核验；建题目时选定后不再更换。"
    "介绍里的样例是说明这类活长什么样的静态文案，不是材料，不能当证据用。"
)

# 这几类活现在装不进这条循环，原因写清楚（docs/24 §1.4 的调研结论）。
# 说清做不了什么，比让人自己撞上去省事：使用者不一定看得出边界在哪。
OUT_OF_SCOPE = (
    {
        "work": "技术尽调",
        "why": "证据是代码仓库、扫描结果、许可证清单，没有「网页原话」这个对象；"
        "结论也是几十项打分算出来的，这条循环不做计算。",
    },
    {
        "work": "组织与人力咨询",
        "why": "三重不合：材料在客户内部；核心产出是编制表和薪酬测算这类算出来的东西；"
        "访谈证据必须匿名化聚合，跟「把原话原样挂上」正好相反。",
    },
    {
        "work": "市场研究的定量部分",
        "why": "开放题编码看着像挂原话，实际是降维——要的不是这句话本身，"
        "是「持这类观点的人占多少」。",
    },
    {
        "work": "竞争情报的常年维护",
        "why": "它不是一轮出一份稿，是一堆长期维护的短卡片；"
        "痛点是一条新事实要同步更新好几张已经发出去的卡，那是另一种形状。"
        "只有「定期深挖出一份稿」那一段装得进来。",
    },
)


# 试问法时撞出来的三类来源陷阱，跟具体模板无关，是这条循环本身的盲点。
# 这个工具的硬门槛是「原话必须逐字来自快照」，但那道门槛**挡不住来源本身
# 是假的**：渠道商的 SEO 页、镜像站、自动生成的工具目录，原话照样扒得出来。
# **扒得出原话，不等于这个来源算数。**
SOURCE_TRAPS = (
    {
        "trap": "扒得出，但不是法定口径",
        "why": "中文长尾里，服务商和渠道商的 SEO 页常排在官方前面，页上真有价格和参数、"
        "也真能逐字扒。挂上去就是把渠道话术当成了官方定价。搜镜像站冒充的更新日志同理。"
        "**存快照之前先看一眼域名是不是当事方自己的。**",
    },
    {
        "trap": "搜得到，但扒不出来",
        "why": "有些平台搜索结果里出现频率极高，却挡爬虫，快照存不下正文——"
        "实测里问答社区、种草社区和招聘站都是这样。这类只能当线索，不能当来源。"
        "**别因为搜得到就以为挂得上。**",
    },
    {
        "trap": "官网抓不到，不等于这家没有公开信息",
        "why": "前端渲染的官网抓下来只有网页标签，正文一个字都拿不到。"
        "这时候要退到应用商店介绍页、帮助中心或开发者文档，而不是记成「这家查无此项」。"
        "**否则会把技术问题误报成事实结论。**",
    },
)


def build_template_list_projection() -> dict[str, Any]:
    """列出新建题目时可选的模板；不暴露内部字段，也不读任何项目数据。

    默认模板必须排在最前并标出来。否则界面上的下拉框会默认选中排序第一个，
    人不动它就静默拿到了另一套问法——这条 2026-08-23 走查时真的踩到过。
    """
    templates = [
        template for template in load_templates().values() if template.listed
    ]
    # 默认的排头，验得深的排在验得浅的前面：人不细看也先碰到验过的那几个。
    order = {"loop_walked": 0, "walked_by_hand": 1, "skeleton": 2}
    templates.sort(
        key=lambda item: (
            item.key != DEFAULT_TEMPLATE_KEY,
            order.get(item.verification, 9),
            item.key,
        )
    )
    return {
        "default_key": DEFAULT_TEMPLATE_KEY,
        "templates": [
            {
                "key": template.key,
                "name": template.name,
                "brief_prompt": template.brief_prompt or "",
                "question_hint_count": len(list(template.recommended_question_labels())),
                "is_default": template.key == DEFAULT_TEMPLATE_KEY,
                # 介绍页要的东西。纯文案，不影响模型怎么理解材料（PRD 20.10）。
                "intro": template.intro,
                "when_to_use": list(template.when_to_use),
                "when_not_to_use": list(template.when_not_to_use),
                "flow": [dict(x) for x in template.flow],
                "steps": [dict(x) for x in template.steps],
                "example": dict(template.example),
                "pitfalls": list(template.pitfalls),
                "question_labels": list(template.recommended_question_labels()),
                # 走查状态和问法来源。这个工具要求主张挂来源，
                # 模板自己的问法没道理例外。
                "verification": template.verification,
                "status_label": VERIFICATION_LEVELS[template.verification],
                # 整条循环走过的才算「验过」。问法试过只说明搜得出东西。
                "loop_walked": template.verification == "loop_walked",
                "status_note": template.status_note,
                "questions": _questions_with_source(template),
            }
            for template in templates
        ],
        "out_of_scope": [dict(item) for item in OUT_OF_SCOPE],
        "source_traps": [dict(item) for item in SOURCE_TRAPS],
        "limitation": _LIMITATION,
    }


def _questions_with_source(template: Any) -> list[dict[str, str]]:
    """七条问法逐条配上来处；没写来处的就写「没注明来处」，不替它编一个。"""
    labels = list(template.recommended_question_labels())
    sources = list(template.provenance)
    return [
        {
            "label": label,
            "source": sources[index] if index < len(sources) else "没注明来处",
        }
        for index, label in enumerate(labels)
    ]
