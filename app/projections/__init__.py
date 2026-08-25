"""同一对象的只读视图投影。

集中导出保持给外部调用者的稳定入口，但不能在包初始化时把所有投影一次导入：
`workbench` 会读取应用层的占位文案，而应用层也会导入具体投影。惰性解析既保留
原 API，也让每个应用模块能够独立导入，不再依赖测试或启动脚本的碰巧顺序。
"""

from importlib import import_module
from typing import Any

__all__ = [
    "build_approved_export_projection",
    "build_brief_projection",
    "build_candidate_source_projection",
    "build_impact_preview",
    "build_report_projection",
    "build_review_context",
    "build_source_list_projection",
    "build_workbench_projection",
]

_EXPORTS = {
    "build_approved_export_projection": (".export", "build_approved_export_projection"),
    "build_brief_projection": (".brief", "build_brief_projection"),
    "build_candidate_source_projection": (".candidates", "build_candidate_source_projection"),
    "build_impact_preview": (".impact", "build_impact_preview"),
    "build_report_projection": (".report", "build_report_projection"),
    "build_review_context": (".report", "build_review_context"),
    "build_source_list_projection": (".sources", "build_source_list_projection"),
    "build_workbench_projection": (".workbench", "build_workbench_projection"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute = target
    value = getattr(import_module(module_name, __name__), attribute)
    globals()[name] = value
    return value
