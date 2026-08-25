from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.adapters.local_source import CaptureError

_MAX_BYTES = 2 * 1024 * 1024
_USER_AGENT = "Jingwei/0.1 (local candidate snapshot)"


class WebPageSourceAdapter:
    """网页快照适配器。只在人工打开并确认升为来源后调用，不把未打开链接写成 Source。"""

    key = "web_page"

    def snapshot(self, url: str, project_files: Path) -> Mapping[str, Any]:
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / "snapshot.bin"
        if destination.exists():
            raise CaptureError("受控副本已存在，拒绝覆盖")
        request = Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urlopen(request, timeout=8) as response:
                body = response.read(_MAX_BYTES + 1)
        except HTTPError as error:
            raise CaptureError(f"打开后未能保存快照：HTTP {error.code}") from error
        except URLError as error:
            raise CaptureError("打开后未能保存快照：无法读取该网页") from error
        except TimeoutError as error:
            raise CaptureError("打开后未能保存快照：读取超时") from error
        if len(body) > _MAX_BYTES:
            raise CaptureError("打开后未能保存快照：页面超过大小上限")
        destination.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        return {
            "file_name": "snapshot.bin",
            "original_url": url,
            "snapshot_path": destination,
            "content_hash": digest,
            "availability": "available",
        }
