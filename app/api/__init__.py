"""本地只读 HTTP API；复用投影，不复制业务状态。"""

from .server import (
    ReadOnlyHttpServer,
    dispatch_delete,
    dispatch_get,
    dispatch_post,
    serve_readonly_api,
)

__all__ = [
    "ReadOnlyHttpServer",
    "dispatch_delete",
    "dispatch_get",
    "dispatch_post",
    "serve_readonly_api",
]
