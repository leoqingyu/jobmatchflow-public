"""
入库之前的完整流水线：JobSpy 抓取 → 漏斗去重（规范化 → 标题向量 → 灰区可选 LLM）。

`jobs_ready_for_ingestion` 在入库前再跑一轮**规范化 + 向量**（不调 LLM；> high 去掉，< low 与灰区均保留）。

不含数据库写入；主应用与调试 API 应通过本模块统一调用，再决定是否 ingest。
"""

from __future__ import annotations

from typing import Optional

from core.logger import get_logger
from core.scrape_params import resolve_scrape_params
from scraper.base import RawJobData
from ai.job_dedup import build_job_dedup_service, dedupe_ordered_step1_before_ingest
from services.scrape_orchestrator import OrchestratorResult, ScrapeOrchestrator

logger = get_logger(__name__)


def run_pre_db_pipeline(
    *,
    countries: list[str],
    hours_old: Optional[int] = None,
    limit_per_call: Optional[int] = None,
    skip_llm: bool = False,
) -> OrchestratorResult:
    """
    跑完抓取 + LLM 去重（除非 skip_llm=True）。
    `jobs_ready_for_ingestion` 会再做入库前规范化 + 向量去重（无 embedding 时退回仅规范化）。
    hours_old / limit_per_call 为 None 时使用 .env（SCRAPE_HOURS_OLD、SCRAPE_LIMIT_PER_SEARCH），见 core.scrape_params。
    """
    h, lim = resolve_scrape_params(hours_old, limit_per_call)

    if not countries:
        empty = OrchestratorResult()
        empty.meta = {
            "hours_old": h,
            "countries": [],
            "skipped": "no_countries",
            "pre_db_pipeline": True,
            "limit_per_call": lim,
            "llm_dedup_skipped": skip_llm,
        }
        logger.info("pre_db：countries 为空，跳过抓取编排（无新岗位）")
        return empty

    orch = ScrapeOrchestrator(hours_old=h, limit_per_call=lim, countries=countries)
    result = orch.run_stages(skip_llm=skip_llm)
    result.meta["pre_db_pipeline"] = True
    result.meta["llm_dedup_skipped"] = skip_llm
    logger.info(f"pre_db 完成: jobspy={len(result.jobs)} skip_llm={skip_llm}")
    return result


def jobs_ready_for_ingestion(result: OrchestratorResult) -> list[RawJobData]:
    """
    入库前按该顺序再做去重：

    - 有 sentence-transformers：`dedupe_ordered_sequence`（规范化整键 + 同公司标题向量；
      相似度 > embed_high 去掉；< embed_low 保留；灰区不调 LLM 一律保留）。
    - 否则：退回仅规范化整键（与原先行为一致）。
    """
    merged = list(result.jobs)
    if not merged:
        return []

    svc = build_job_dedup_service(skip_llm=True)
    if svc is not None:
        kept, removed_idx = svc.dedupe_ordered_sequence(merged)
        logger.info(
            "入库前合并去重（规范化+向量，无 LLM）: 输入=%s 保留=%s 去掉=%s",
            len(merged),
            len(kept),
            len(removed_idx),
        )
        return kept

    out = dedupe_ordered_step1_before_ingest(merged)
    logger.info(
        "入库前合并去重（无向量依赖，仅规范化）: 输入=%s 输出=%s",
        len(merged),
        len(out),
    )
    return out
