"""
生成策略配置占位（读写独立接口，见 api/web_routes.py GET/PUT /settings/generation-policy）。

当前简历/求职信生成模型固定为 core/config.py 里配置的档位，不按请求选择；本文件仅持久化
占位结构，供以后扩展（例如按分数/用户强制模型）时再接入业务逻辑。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_POLICY_PATH = _PROJECT_ROOT / "secrets" / "generation_policy.json"

_DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    "description": (
        "预留配置：例如按岗位分、用户维度强制模型等。"
        "当前实现中，生成模型固定读 core/config.py 配置，本文件不参与覆盖。"
    ),
    "rules": [],
}


def default_generation_policy() -> dict[str, Any]:
    return deepcopy(_DEFAULT_POLICY)


def read_generation_policy() -> dict[str, Any]:
    if not _POLICY_PATH.is_file():
        return default_generation_policy()
    try:
        raw = json.loads(_POLICY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_generation_policy()
    if not isinstance(raw, dict):
        return default_generation_policy()
    merged = default_generation_policy()
    merged.update(raw)
    return merged


def write_generation_policy(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise ValueError("generation_policy 须为 JSON 对象")
    _POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _POLICY_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
