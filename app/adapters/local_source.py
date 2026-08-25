from __future__ import annotations

import hashlib
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class CaptureError(ValueError):
    pass


MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class LocalFileSourceAdapter:
    key = "local_file"

    def capture(self, source: Path, project_files: Path) -> Mapping[str, Any]:
        source = Path(source)
        if not source.exists():
            raise CaptureError(f"文件不存在：{source}")
        if not source.is_file():
            raise CaptureError(f"不是可捕获的文件：{source}")
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / source.name
        if destination.exists():
            raise CaptureError("受控副本已存在，拒绝覆盖")
        shutil.copy2(source, destination)
        return {
            "file_name": source.name,
            "original_path": str(source.resolve()),
            "snapshot_path": destination,
            "content_hash": sha256_file(destination),
            "title": source.stem,
        }

    def capture_bytes(self, file_name: str, data: bytes, project_files: Path) -> Mapping[str, Any]:
        safe_name = Path(str(file_name or "")).name.strip()
        if not safe_name or safe_name in {".", ".."}:
            raise CaptureError("文件名无效")
        if not data:
            raise CaptureError("文件内容为空")
        if len(data) > MAX_UPLOAD_BYTES:
            raise CaptureError("文件过大，请不超过 20MB")
        project_files = Path(project_files)
        project_files.mkdir(parents=True, exist_ok=True)
        destination = project_files / safe_name
        if destination.exists():
            raise CaptureError("受控副本已存在，拒绝覆盖")
        destination.write_bytes(data)
        return {
            "file_name": safe_name,
            "original_path": safe_name,
            "snapshot_path": destination,
            "content_hash": sha256_file(destination),
            "title": Path(safe_name).stem or safe_name,
        }


class DeferredParser:
    """捕获阶段不解析摘录，避免把未核验文字写入 EvidenceExcerpt。"""

    key = "deferred"

    def parse(self, captured_source: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        return []
