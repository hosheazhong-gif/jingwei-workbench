from __future__ import annotations

import re


class MultipartError(ValueError):
    pass


def parse_multipart_form(
    content_type: str, body: bytes
) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """解析浏览器上传的 multipart/form-data。不引入第三方包。"""
    match = re.search(r"boundary=([^;]+)", content_type or "", re.I)
    if not match:
        raise MultipartError("缺少 multipart boundary")
    boundary = match.group(1).strip().strip('"').encode("utf-8")
    if not boundary:
        raise MultipartError("缺少 multipart boundary")
    delimiter = b"--" + boundary
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    for raw_part in body.split(delimiter)[1:]:
        if raw_part.startswith(b"--"):
            break
        raw_part = raw_part.lstrip(b"\r\n")
        if not raw_part:
            continue
        header_blob, separator, content = raw_part.partition(b"\r\n\r\n")
        if not separator:
            continue
        if content.endswith(b"\r\n"):
            content = content[:-2]
        headers = header_blob.decode("utf-8", errors="replace")
        name_match = re.search(r'name="([^"]+)"', headers)
        if not name_match:
            continue
        name = name_match.group(1)
        file_match = re.search(r'filename="([^"]*)"', headers)
        if file_match is not None:
            files[name] = (file_match.group(1), content)
        else:
            fields[name] = content.decode("utf-8")
    return fields, files
