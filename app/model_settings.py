from __future__ import annotations

import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.adapters.http_draft import (
    DEFAULT_DRAFT_PROVIDER,
    DRAFT_PROVIDERS,
    resolve_draft_endpoint,
)
from app.application.draft_suggestion import DraftSuggestionError
from app.local_env import update_local_env


MODEL_ENV_KEYS = (
    "JINGWEI_DRAFT_PROVIDER",
    "JINGWEI_DRAFT_API_KEY",
    "JINGWEI_DRAFT_URL",
    "JINGWEI_DRAFT_MODEL",
)
_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "deepseek": "DeepSeek",
    "moonshot": "Kimi / Moonshot",
    "qwen": "通义千问",
    "glm": "智谱 GLM",
    "custom": "自定义兼容接口",
}


class ModelSettingsError(ValueError):
    pass


def model_settings_root(repository: Any) -> Path:
    return Path(repository.database_path).resolve().parent


def read_model_settings(
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    try:
        provider, url, model = resolve_draft_endpoint(env)
    except DraftSuggestionError as error:
        raise ModelSettingsError(str(error).replace("没有改内部稿。", "").strip()) from error
    api_key_set = bool(str(env.get("JINGWEI_DRAFT_API_KEY") or "").strip())
    providers = [
        {
            "key": key,
            "label": _PROVIDER_LABELS[key],
            "default_url": value["url"],
            "default_model": value["model"],
        }
        for key, value in DRAFT_PROVIDERS.items()
    ]
    providers.append(
        {
            "key": "custom",
            "label": _PROVIDER_LABELS["custom"],
            "default_url": "",
            "default_model": "",
        }
    )
    return {
        "provider": provider,
        "url": url,
        "model": model,
        "api_key_set": api_key_set,
        "providers": providers,
        "message": "模型已配置" if api_key_set else "尚未配置模型 API Key",
    }


def save_model_settings(
    root: Path,
    payload: Mapping[str, Any],
    environ: MutableMapping[str, str] | None = None,
) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    provider = str(payload.get("provider") or DEFAULT_DRAFT_PROVIDER).strip().lower()
    if provider not in {*DRAFT_PROVIDERS, "custom"}:
        raise ModelSettingsError("不支持这个模型服务")

    api_key_supplied = "api_key" in payload and bool(
        str(payload.get("api_key") or "").strip()
    )
    clear_api_key = bool(payload.get("clear_api_key"))
    current_api_key = str(env.get("JINGWEI_DRAFT_API_KEY") or "").strip()
    api_key = (
        str(payload.get("api_key") or "").strip()
        if api_key_supplied
        else current_api_key
    )
    if clear_api_key:
        api_key = ""
    elif not api_key:
        raise ModelSettingsError("请填写 API Key")

    url = str(payload.get("url") or "").strip()
    model = str(payload.get("model") or "").strip()
    if url:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ModelSettingsError("接口地址必须是完整的 http:// 或 https:// 地址")
    if provider == "custom":
        if not url:
            raise ModelSettingsError("自定义服务需要填写接口地址")
        if not model:
            raise ModelSettingsError("自定义服务需要填写模型名称")

    updates: dict[str, str | None] = {
        "JINGWEI_DRAFT_PROVIDER": provider,
        "JINGWEI_DRAFT_URL": url or None,
        "JINGWEI_DRAFT_MODEL": model or None,
    }
    if clear_api_key:
        updates["JINGWEI_DRAFT_API_KEY"] = None
    elif api_key_supplied:
        updates["JINGWEI_DRAFT_API_KEY"] = api_key

    try:
        update_local_env(root, updates)
    except (OSError, ValueError) as error:
        raise ModelSettingsError("模型设置没能保存到本机") from error

    for key, value in updates.items():
        if value:
            env[key] = value
        else:
            env.pop(key, None)
    return read_model_settings(env)
