"""
每用户多密钥（Claude / Gemini Pro / Gemini Lite）的 JSON 配置。

复制 secrets/llm_api_keys.example.json → secrets/llm_api_keys.json 并填写；
后续可迁库为每用户三列 API Key。未在文件或 env 中配置的密钥在调用对应模型时会报错。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_KEYS_PATH = _PROJECT_ROOT / "secrets" / "llm_api_keys.json"


@dataclass(frozen=True)
class UserLLMKeys:
    claude_api_key: str
    gemini_pro_api_key: str
    gemini_lite_api_key: str


def _load_raw() -> dict[str, Any]:
    if not _KEYS_PATH.is_file():
        return {}
    try:
        return json.loads(_KEYS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def load_user_llm_keys(user_id: int) -> UserLLMKeys:
    data = _load_raw()
    users = data.get("users") or {}
    u = users.get(str(user_id))
    if u is None and user_id in users:
        u = users[user_id]
    u = u or {}
    return UserLLMKeys(
        claude_api_key=(u.get("claude_api_key") or "").strip(),
        gemini_pro_api_key=(u.get("gemini_api_key") or "").strip(),
        gemini_lite_api_key=(u.get("gemini_lite_api_key") or "").strip(),
    )
