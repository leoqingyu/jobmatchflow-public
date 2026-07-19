"""
抓取时间与条数：**统一从 settings（.env）解析**，供流水线、Debug API 等共用。

环境变量（pydantic-settings 映射到字段名）：
- **SCRAPE_HOURS_OLD** → `settings.scrape_hours_old`
  - 设为整数：JobSpy `hours_old`、编排均用该值。
  - **未设置**（None）：有效窗口为 **24 小时**（代码默认；调度失败/延迟需手动补抓）。仅供旧的
    手动全量触发（`PipelineService.run_full_pipeline`）/调试用；分时段调度任务
    （`tasks/scheduler.py`）各自显式传 `hours_old`（热门州/卢森堡 24h，尾部州 168h），
    不读这个默认值。
- **SCRAPE_LIMIT_PER_SEARCH** → `settings.scrape_limit_per_search`
  - 每国家每轮 JobSpy `results_wanted` 上限。

单次请求若显式传入 `hours_old` / `limit`，则覆盖 .env（仅当次）。
"""

from __future__ import annotations

import random
from typing import Optional

from core.config import settings

# 未配置 SCRAPE_HOURS_OLD 时的默认小时数
_DEFAULT_HOURS_WHEN_UNSET = 24


def effective_scrape_hours() -> int:
    """当前生效的抓取时间窗（小时）。未配置 scrape_hours_old 时为 24。"""
    if settings.scrape_hours_old is not None:
        return int(settings.scrape_hours_old)
    return _DEFAULT_HOURS_WHEN_UNSET


def effective_scrape_limit() -> int:
    """当前生效的每轮条数上限（与 JobSpy results_wanted 对齐）。"""
    return int(settings.scrape_limit_per_search)


def resolve_scrape_params(
    hours_old: Optional[int] = None,
    limit_per_call: Optional[int] = None,
) -> tuple[int, int]:
    """
    解析本次抓取使用的 (hours_old, limit)。
    参数为 None 时用 .env / settings；传入正整数则仅本次覆盖。
    """
    h = int(hours_old) if hours_old is not None else effective_scrape_hours()
    lim = int(limit_per_call) if limit_per_call is not None else effective_scrape_limit()
    return h, lim


def random_scrape_delay() -> float:
    """
    请求之间的随机等待秒数（SCRAPE_REQUEST_DELAY_MIN_SEC ~ MAX_SEC，默认 5~15s）。
    各分时段抓取任务之间有几个小时余量，不用抓得快，宁可等久一点，避免固定节奏的请求
    模式太像脚本。
    """
    lo = float(settings.scrape_request_delay_min_sec)
    hi = float(settings.scrape_request_delay_max_sec)
    if hi <= lo:
        return lo
    return random.uniform(lo, hi)
