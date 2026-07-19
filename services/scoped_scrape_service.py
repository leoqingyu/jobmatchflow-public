"""
分时段/分域抓取任务用的抓取入口。

与 `services.scrape_orchestrator.ScrapeOrchestrator` 的区别：编排器一次调用内自行做批内查重；
这里每个时段独立触发，查重的"母库"改成数据库里最近一段时间已入库的同国家岗位
（见 `services.ingestion_service.load_recent_jobs_as_raw`），而不是同一次调用里的其它结果。
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from core.logger import get_logger
from ai.job_dedup import build_job_dedup_service, filter_new_only_step1
from scraper.base import BaseScraperProvider
from services.ingestion_service import (
    DEDUP_LOOKBACK_DAYS,
    JobIngestionService,
    load_recent_jobs_as_raw,
)

logger = get_logger(__name__)


def scrape_source_and_ingest(
    db: Session,
    provider: BaseScraperProvider,
    *,
    keywords: Optional[list[str]] = None,
    countries: list[str],
    hours_old: int,
    limit: Optional[int] = None,
    dedup_lookback_days: int = DEDUP_LOOKBACK_DAYS,
    skip_llm: bool = False,
    log_source: str,
) -> dict:
    """
    抓一轮 → 与数据库里最近 dedup_lookback_days 天的同国家岗位跑漏斗去重 → 入库。
    """
    raw = provider.fetch_jobs(keywords=keywords or [], countries=countries, limit=limit)
    logger.info("scoped scrape: source=%s countries=%s 抓到 %s 条", log_source, countries, len(raw))

    mother_recent = load_recent_jobs_as_raw(db, countries, dedup_lookback_days)
    dedup = build_job_dedup_service(skip_llm)
    if dedup:
        kept, removed_idx = dedup.filter_new_only(mother_recent, raw)
    else:
        kept, removed_idx = filter_new_only_step1(mother_recent, raw)
    logger.info(
        "scoped scrape: source=%s 跨源查重（母库=%s 条）去掉 %s 条，剩 %s 条待入库",
        log_source,
        len(mother_recent),
        len(removed_idx),
        len(kept),
    )

    ingestion = JobIngestionService(db)
    result = ingestion.ingest_raw_jobs(
        kept,
        log_source=log_source,
        countries=countries,
        keyword_note=f"scoped_{hours_old}h",
    )
    result["fetched_raw"] = len(raw)
    result["cross_source_dedup_removed"] = len(removed_idx)
    return result
