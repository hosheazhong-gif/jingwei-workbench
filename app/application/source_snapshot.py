from __future__ import annotations

from html import escape, unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from app.adapters.sqlite_repository import SqliteRepository

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HTML_HEADS = (b"<!doctype html", b"<html", b"<head", b"<body")
# 网页快照一律落盘成 snapshot.bin，后缀说明不了任何事。从公开搜索抓回来的
# PDF 曾经因此被当成 HTML：抽“正文”抽出一堆二进制垃圾，长度过了门槛，于是
# 「看快照 / 从快照扒原话」两个键照给，点下去必然是死路。所以先认文件头。
_PDF_HEAD = b"%PDF-"
_IMAGE_HEADS = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
_TEXT_SUFFIXES = {".txt", ".md", ".csv"}
_SKIP_TAGS = {"script", "style", "noscript", "template"}
_MAX_PAGE_LINKS = 40
# 一段原话至少要这么长才收得下；快照正文短于它，就绝不可能从里面摘出
# 任何一句，「看快照 / 从快照扒原话」也就不该出现在卡片上。
MIN_SNAPSHOT_BODY_CHARS = 8
_SNAPSHOT_CACHE_LIMIT = 256
_SNAPSHOT_BODY_CACHE: dict[tuple[str, int, int], tuple[bool, bool, str | None]] = {}
_BREAK_TAGS = {
    "p",
    "div",
    "br",
    "li",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "section",
    "article",
}


class SnapshotError(ValueError):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BREAK_TAGS and not self._skip:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BREAK_TAGS and not self._skip:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


class _LinkExtractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url.strip()
        self.links: list[str] = []
        self._seen: set[str] = set()
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip or tag != "a" or len(self.links) >= _MAX_PAGE_LINKS:
            return
        href = ""
        for key, value in attrs:
            if key == "href":
                href = str(value or "").strip()
                break
        absolute = _absolute_http_url(href, self.base_url)
        if not absolute or absolute in self._seen:
            return
        self._seen.add(absolute)
        self.links.append(absolute)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1


def read_source_snapshot(
    repository: SqliteRepository, source_id: str
) -> tuple[bytes, str]:
    """读取已保存的受控快照。不改稿，不改核验，也不解析成第二套结论。"""
    source = repository.get_source(source_id)
    if source is None:
        raise SnapshotError(f"来源 {source_id} 不存在")
    path = resolve_source_snapshot_path(repository, source)
    if path is None:
        raise SnapshotError("没有可打开的网页快照。先打开链接并用作依据。")
    body = path.read_bytes()
    return body, _content_type(path, str(source.get("kind") or ""), body)


def build_snapshot_view(
    repository: SqliteRepository, source_id: str
) -> tuple[bytes, str]:
    """给工作台看的快照页。网页抽出正文；图片和 PDF 仍给原件。"""
    source = repository.get_source(source_id)
    if source is None:
        raise SnapshotError(f"来源 {source_id} 不存在")
    body, content_type = read_source_snapshot(repository, source_id)
    mime = content_type.split(";")[0].strip().lower()
    if mime.startswith("image/") or mime == "application/pdf":
        return body, content_type
    text = snapshot_plain_text(body, content_type)
    title = str(source.get("title") or "未命名材料").strip() or "未命名材料"
    url = str(source.get("original_url") or "").strip()
    links = snapshot_page_links(body, content_type, base_url=url)
    page = _readable_snapshot_page(title, url, text, links)
    return page.encode("utf-8"), "text/html; charset=utf-8"


def snapshot_plain_text(body: bytes, content_type: str = "") -> str:
    """从受控快照抽出可读正文。跳过脚本样式，不写成结论。"""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime.startswith("image/") or mime == "application/pdf":
        return ""
    raw = _decode_bytes(body)
    head = raw.lstrip()[:200].lower()
    if (
        mime in {"text/html", "application/xhtml+xml"}
        or not mime
        or head.startswith("<!doctype html")
        or head.startswith("<html")
        or "<html" in head
    ):
        raw = _html_to_text(raw)
    lines = [" ".join(line.split()) for line in raw.replace("\r\n", "\n").split("\n")]
    collapsed: list[str] = []
    blank = False
    for line in lines:
        if not line:
            if collapsed and not blank:
                collapsed.append("")
                blank = True
            continue
        collapsed.append(line)
        blank = False
    return "\n".join(collapsed).strip()


def snapshot_page_links(
    body: bytes, content_type: str = "", *, base_url: str | None = None
) -> list[str]:
    """抽出快照里的 http(s) 链接，供人打开原页。不写进稿。"""
    mime = (content_type or "").split(";")[0].strip().lower()
    if mime.startswith("image/") or mime == "application/pdf":
        return []
    raw = _decode_bytes(body)
    parser = _LinkExtractor(base_url or "")
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return []
    return parser.links


def snapshot_is_viewable(
    repository: SqliteRepository, source: dict[str, Any]
) -> bool:
    return bool(snapshot_capabilities(repository, source)["can_view_snapshot"])


def snapshot_capabilities(
    repository: SqliteRepository, source: dict[str, Any]
) -> dict[str, Any]:
    """这份受控副本能不能看、能不能从里面扒原话。

    存下了文件不等于存下了正文：重 JS 的站点常常只剩壳。抽不出正文时两个
    动作都不给，卡片上直接说只能打开链接，不让人点下去才发现是死路。
    图片和 PDF 仍可看原件，但不能扒原话。
    """
    path = resolve_source_snapshot_path(repository, source)
    if path is None:
        return {
            "can_view_snapshot": False,
            "can_scrape_snapshot": False,
            "snapshot_note": "这份没有留下受控副本，只能打开链接。",
        }
    try:
        stat = path.stat()
    except OSError:
        return {
            "can_view_snapshot": False,
            "can_scrape_snapshot": False,
            "snapshot_note": "这份的受控副本已经找不到了，只能打开链接。",
        }
    key = (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    cached = _SNAPSHOT_BODY_CACHE.get(key)
    if cached is None:
        body = path.read_bytes()
        content_type = _content_type(path, str(source.get("kind") or ""), body)
        mime = content_type.split(";")[0].strip().lower()
        if mime == "application/pdf":
            # PDF 能看原件，但本机不解析它，扒不出可逐字引用的句子。
            # 说清楚，人才知道该手工粘原句，而不是以为按钮坏了。
            cached = (True, False, "这份是 PDF：能看原件，但扒不出原话，要引用请手工粘。")
        elif mime.startswith("image/"):
            cached = (True, False, "这份是图片：能看原件，但扒不出原话，要引用请手工粘。")
        else:
            text = " ".join(snapshot_plain_text(body, content_type).split())
            has_body = len(text) >= MIN_SNAPSHOT_BODY_CHARS
            cached = (
                has_body,
                has_body,
                None if has_body else "这份没能存下可读正文，只能打开链接。",
            )
        if len(_SNAPSHOT_BODY_CACHE) >= _SNAPSHOT_CACHE_LIMIT:
            _SNAPSHOT_BODY_CACHE.clear()
        _SNAPSHOT_BODY_CACHE[key] = cached
    can_view, can_scrape, note = cached
    return {
        "can_view_snapshot": can_view,
        "can_scrape_snapshot": can_scrape,
        "snapshot_note": note,
    }


def resolve_source_snapshot_path(
    repository: SqliteRepository, source: dict[str, Any]
) -> Path | None:
    raw = str(source.get("snapshot_path") or "").strip()
    if not raw:
        return None
    relative = Path(raw)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    files_root = repository.files_root.resolve()
    under_files = (files_root / relative).resolve()
    try:
        under_files.relative_to(files_root)
        if under_files.is_file():
            return under_files
    except ValueError:
        pass
    under_repo = (_REPO_ROOT / relative).resolve()
    try:
        under_repo.relative_to(_REPO_ROOT)
        if under_repo.is_file():
            return under_repo
    except ValueError:
        pass
    return None


def _html_to_text(raw: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return unescape(raw)
    return unescape("".join(parser.parts))


def _decode_bytes(body: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _readable_snapshot_page(
    title: str, url: str, text: str, links: list[str] | None = None
) -> str:
    body = text or "这份快照没有可抽出的正文。"
    url_line = _anchor_paragraph(url, "原链接：") if url else ""
    extra = [item for item in (links or []) if item != url]
    link_block = ""
    if extra:
        items = "".join(
            f"<li>{_anchor(item, item)}</li>\n" for item in extra
        )
        link_block = (
            '<h2 class="meta" style="margin:1.4rem 0 .4rem;font-size:.95rem;">页里的链接</h2>\n'
            f"<ul class=\"links\">{items}</ul>\n"
        )
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN">\n'
        "<head>\n"
        '<meta charset="utf-8" />\n'
        f"<title>快照 · {escape(title)}</title>\n"
        "<style>\n"
        "body{font:17px/1.6 \"Segoe UI\",\"PingFang SC\",\"Noto Sans SC\",sans-serif;"
        "max-width:46rem;margin:2rem auto;padding:0 1.2rem;color:#141310;background:#fbfaf6;}\n"
        ".meta{color:#6a655e;font-size:.9rem;}\n"
        "h1{font-size:1.35rem;letter-spacing:-.03em;}\n"
        "a{color:#1a5c42;}\n"
        "ul.links{padding-left:1.2rem;word-break:break-all;}\n"
        "pre{white-space:pre-wrap;word-break:break-word;font:inherit;margin:1.2rem 0 0;}\n"
        "</style>\n"
        "</head>\n"
        "<body>\n"
        '<p class="meta">保存的网页快照，不是给经理的稿。</p>\n'
        f"<h1>{escape(title)}</h1>\n"
        f"{url_line}\n"
        f"<pre>{escape(body)}</pre>\n"
        f"{link_block}"
        "</body>\n"
        "</html>\n"
    )


def _anchor_paragraph(url: str, prefix: str) -> str:
    return f'<p class="meta">{escape(prefix)}{_anchor(url, url)}</p>'


def _anchor(url: str, label: str) -> str:
    href = _absolute_http_url(url, "")
    if not href:
        return escape(label)
    return (
        f'<a href="{escape(href, quote=True)}" target="_blank" rel="noopener">'
        f"{escape(label)}</a>"
    )


def _absolute_http_url(href: str, base_url: str) -> str | None:
    raw = str(href or "").strip()
    if not raw or raw.startswith("#") or raw.lower().startswith(
        ("javascript:", "mailto:", "data:", "vbscript:")
    ):
        return None
    try:
        absolute = urljoin(base_url, raw) if base_url else raw
        parsed = urlparse(absolute)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return absolute


def _content_type(path: Path, kind: str, body: bytes) -> str:
    # 文件头先说话：后缀和 kind 都可能骗人（网页快照永远叫 snapshot.bin）。
    if body.startswith(_PDF_HEAD):
        return "application/pdf"
    for head, mime in _IMAGE_HEADS:
        if body.startswith(head):
            return mime
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    if suffix == ".pdf":
        return "application/pdf"
    if suffix in _IMAGE_SUFFIXES:
        return {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }[suffix]
    if suffix in _TEXT_SUFFIXES:
        return "text/plain; charset=utf-8"
    head = body.lstrip()[:32].lower()
    if kind == "web_page" or any(head.startswith(item) for item in _HTML_HEADS):
        return "text/html; charset=utf-8"
    return "application/octet-stream"
