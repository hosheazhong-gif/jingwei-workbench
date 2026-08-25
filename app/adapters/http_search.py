from __future__ import annotations

import html
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from http.cookiejar import CookieJar
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen


class SearchAdapterError(ValueError):
    pass


class SearchChallengeError(SearchAdapterError):
    pass


DEFAULT_SEARCH_PROVIDER = "ddg"
DUCKDUCKGO_HOME_URL = "https://duckduckgo.com/"
DUCKDUCKGO_HTML_URL = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_LITE_URL = "https://lite.duckduckgo.com/lite/"
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
MAX_HITS = 4
CHALLENGE_MESSAGE = (
    "公开搜索被拦截。可在 .env 写下 JINGWEI_SEARCH_PROVIDER=brave 和 "
    "JINGWEI_SEARCH_API_KEY 后再试。没有写入候选，也没有改稿。"
)
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_ANCHOR = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.I | re.S)
_CLASS = re.compile(r"""\bclass\s*=\s*["']([^"']+)["']""", re.I)
_HREF = re.compile(r"""\bhref\s*=\s*["']([^"']+)["']""", re.I)
_TAGS = re.compile(r"<[^>]+>")
_BLOCKED_HOSTS = (
    "duckduckgo.com",
    "www.bing.com",
    "bing.com",
    "google.com",
    "www.google.com",
)


class DuckDuckGoHtmlSearchAdapter:
    """公开网页搜索。只返回真实结果链接，不把摘要写成来源。"""

    key = "duckduckgo_html"

    def __init__(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: int = 20,
    ) -> None:
        self._opener = opener or urlopen
        self._timeout = timeout

    def search(self, query: str) -> Sequence[Mapping[str, Any]]:
        text = str(query or "").strip()
        if not text:
            return []
        url = DUCKDUCKGO_HTML_URL + "?" + urlencode({"q": text})
        request = Request(
            url,
            method="GET",
            headers=_search_headers(referer="https://html.duckduckgo.com/"),
        )
        body = _read_html(self._opener, request, self._timeout)
        _raise_if_challenged(body)
        return parse_duckduckgo_html(body)[:MAX_HITS]


class DuckDuckGoLiteSearchAdapter:
    """公开网页搜索（精简页）。只返回真实结果链接，不把摘要写成来源。"""

    key = "duckduckgo_lite"

    def __init__(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: int = 20,
    ) -> None:
        self._opener = opener or urlopen
        self._timeout = timeout

    def search(self, query: str) -> Sequence[Mapping[str, Any]]:
        text = str(query or "").strip()
        if not text:
            return []
        url = DUCKDUCKGO_LITE_URL + "?" + urlencode({"q": text})
        request = Request(
            url,
            method="GET",
            headers=_search_headers(referer="https://duckduckgo.com/"),
        )
        body = _read_html(self._opener, request, self._timeout)
        _raise_if_challenged(body)
        return parse_duckduckgo_lite(body)[:MAX_HITS]


class PublicHtmlSearchAdapter:
    """默认公开搜索：先打开主页再搜精简页；仍空才试 HTML 页。不把验证码页当成没搜到。"""

    key = "public_html"

    def __init__(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: int = 20,
    ) -> None:
        self._opener = opener or _cookie_opener()
        self._timeout = timeout
        self._lite = DuckDuckGoLiteSearchAdapter(opener=self._opener, timeout=timeout)
        self._html = DuckDuckGoHtmlSearchAdapter(opener=self._opener, timeout=timeout)

    def search(self, query: str) -> Sequence[Mapping[str, Any]]:
        self._warmup(query)
        hits, lite_error = self._try_search(self._lite, query)
        if hits:
            return hits
        if isinstance(lite_error, SearchChallengeError):
            self._warmup(query)
            hits, lite_error = self._try_search(self._lite, query)
            if hits:
                return hits
        hits, html_error = self._try_search(self._html, query)
        if hits:
            return hits
        if isinstance(lite_error, SearchChallengeError) and (
            html_error is None or isinstance(html_error, SearchChallengeError)
        ):
            raise SearchChallengeError(CHALLENGE_MESSAGE)
        if html_error is not None:
            raise html_error
        if lite_error is not None:
            raise lite_error
        return []

    def _warmup(self, query: str) -> None:
        text = str(query or "").strip()
        if not text:
            return
        request = Request(
            DUCKDUCKGO_HOME_URL + "?" + urlencode({"q": text}),
            method="GET",
            headers=_search_headers(referer="https://duckduckgo.com/"),
        )
        try:
            _read_html(self._opener, request, self._timeout)
        except SearchAdapterError:
            return

    def _try_search(
        self, adapter: DuckDuckGoLiteSearchAdapter | DuckDuckGoHtmlSearchAdapter, query: str
    ) -> tuple[list[dict[str, Any]], SearchAdapterError | None]:
        try:
            return list(adapter.search(query) or []), None
        except SearchAdapterError as error:
            return [], error


class BraveSearchAdapter:
    """Brave Search API。只返回真实结果链接，不把摘要写成来源。"""

    key = "brave"

    def __init__(
        self,
        *,
        api_key: str,
        opener: Callable[..., Any] | None = None,
        timeout: int = 20,
    ) -> None:
        self._api_key = api_key
        self._opener = opener or urlopen
        self._timeout = timeout

    def search(self, query: str) -> Sequence[Mapping[str, Any]]:
        text = str(query or "").strip()
        if not text:
            return []
        url = BRAVE_SEARCH_URL + "?" + urlencode({"q": text, "count": str(MAX_HITS)})
        request = Request(
            url,
            method="GET",
            headers={
                "Accept": "application/json",
                "X-Subscription-Token": self._api_key,
            },
        )
        raw = _read_response(self._opener, request, self._timeout)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SearchAdapterError("搜索结果无法阅读，没有写入候选，也没有改稿。") from error
        if not isinstance(payload, dict):
            raise SearchAdapterError("搜索结果无法阅读，没有写入候选，也没有改稿。")
        return parse_brave_payload(payload)[:MAX_HITS]


def parse_duckduckgo_html(body: str) -> list[dict[str, str]]:
    return _parse_result_anchors(body, {"result__a"})


def parse_duckduckgo_lite(body: str) -> list[dict[str, str]]:
    return _parse_result_anchors(body, {"result-link"})


def parse_brave_payload(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    web = payload.get("web")
    results = web.get("results") if isinstance(web, dict) else None
    if not isinstance(results, list):
        return []
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in results:
        if not isinstance(item, dict):
            continue
        url = unwrap_result_url(str(item.get("url") or ""))
        if not url or url in seen:
            continue
        title = str(item.get("title") or "").strip() or url
        snippet = str(item.get("description") or "").strip()
        hits.append({"url": url, "title": title, "snippet": snippet})
        seen.add(url)
    return hits


def looks_like_search_challenge(body: str) -> bool:
    text = (body or "").lower()
    return (
        "anomaly.js" in text
        or 'id="challenge-form"' in text
        or "id='challenge-form'" in text
        or "anomaly-modal" in text
    )


def unwrap_result_url(href: str) -> str | None:
    text = html.unescape(str(href or "")).strip()
    if not text:
        return None
    if text.startswith("//"):
        text = "https:" + text
    parsed = urlparse(text)
    if "duckduckgo.com" in (parsed.netloc or "").lower() and parsed.path.startswith("/l"):
        target = (parse_qs(parsed.query).get("uddg") or [""])[0]
        if target:
            text = unquote(target)
            parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    host = parsed.netloc.lower()
    if any(host == blocked or host.endswith("." + blocked) for blocked in _BLOCKED_HOSTS):
        return None
    return text.split("#", 1)[0]


def resolve_search_adapter(
    environ: Mapping[str, str] | None = None,
    opener: Callable[..., Any] | None = None,
) -> PublicHtmlSearchAdapter | DuckDuckGoHtmlSearchAdapter | DuckDuckGoLiteSearchAdapter | BraveSearchAdapter:
    env = environ if environ is not None else os.environ
    provider = str(env.get("JINGWEI_SEARCH_PROVIDER") or DEFAULT_SEARCH_PROVIDER).strip().lower()
    if provider in {"brave", "search.brave"}:
        api_key = str(env.get("JINGWEI_SEARCH_API_KEY") or "").strip()
        if not api_key:
            raise SearchAdapterError(
                "Brave 搜索需要 JINGWEI_SEARCH_API_KEY。也可不设 JINGWEI_SEARCH_PROVIDER，改用默认公开搜索。"
                "没有写入候选，也没有改稿。"
            )
        return BraveSearchAdapter(api_key=api_key, opener=opener)
    if provider in {"ddg-html", "duckduckgo-html"}:
        return DuckDuckGoHtmlSearchAdapter(opener=opener)
    if provider in {"ddg-lite", "duckduckgo-lite"}:
        return DuckDuckGoLiteSearchAdapter(opener=opener)
    if provider not in {"", "ddg", "duckduckgo", "public"}:
        raise SearchAdapterError(
            "还不认识这个搜索服务。可用 ddg 或 brave。没有写入候选，也没有改稿。"
        )
    return PublicHtmlSearchAdapter(opener=opener)


def _parse_result_anchors(body: str, class_tokens: set[str]) -> list[dict[str, str]]:
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for match in _ANCHOR.finditer(body or ""):
        class_attr = _CLASS.search(match.group(1) or "")
        if class_attr is None:
            continue
        classes = set(class_attr.group(1).split())
        if not (classes & class_tokens):
            continue
        href = _HREF.search(match.group(1) or "")
        if href is None:
            continue
        url = unwrap_result_url(href.group(1))
        if not url or url in seen:
            continue
        title = _TAGS.sub("", html.unescape(match.group(2) or "")).strip() or url
        hits.append({"url": url, "title": title, "snippet": ""})
        seen.add(url)
    return hits


def _search_headers(*, referer: str) -> dict[str, str]:
    return {
        "User-Agent": _USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": referer,
    }


def _raise_if_challenged(body: str) -> None:
    if looks_like_search_challenge(body):
        raise SearchChallengeError(CHALLENGE_MESSAGE)


def _read_html(opener: Callable[..., Any], request: Request, timeout: int) -> str:
    raw = _read_response(opener, request, timeout)
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception as error:
        raise SearchAdapterError("搜索结果无法阅读，没有写入候选，也没有改稿。") from error


def _read_response(opener: Callable[..., Any], request: Request, timeout: int) -> bytes:
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read(512 * 1024 + 1)
    except HTTPError as error:
        raise SearchAdapterError("没搜到。请检查本机网络后再试。没有写入候选，也没有改稿。") from error
    except URLError as error:
        raise SearchAdapterError("没搜到。请检查本机网络后再试。没有写入候选，也没有改稿。") from error
    except TimeoutError as error:
        raise SearchAdapterError("搜索超时。没有写入候选，也没有改稿。") from error
    if len(raw) > 512 * 1024:
        raise SearchAdapterError("搜索结果过长，没有写入候选，也没有改稿。")
    return raw


def _cookie_opener() -> Callable[..., Any]:
    return build_opener(HTTPCookieProcessor(CookieJar())).open
