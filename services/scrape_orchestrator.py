"""
抓取编排：**JobSpy（单一来源）**。

countries 来自 Settings「求职国家」多选（user_search_profiles.countries）；为空则 pre_db_pipeline 层直接跳过。

1. **抓取**：按「地区 → 语言」「语言 × 领域（计算机/金融/交叉） → 关键词」抓取（见 `core.search_keywords`），
   不再是纯时间窗宽搜；**瑞士**按 26 州 + 25 km 半径、各州按语言组关键词循环，**卢森堡** EN+FR 关键词、单点 35 km，
   其它国家暂无专门关键词表、退回该国默认语言（当前只是过渡，重点仍是瑞士 + 卢森堡的计算机/金融岗位）；
   JobSpy 返回结果在 Provider 内按 `(source, external_job_id)` 去重。
2. **入库前**：合并列表再跑一轮**规范化 + 向量**去重，无 embedding 依赖时退回仅规范化（见 `jobs_ready_for_ingestion`）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from core.logger import get_logger
from core.scrape_params import effective_scrape_limit
from ai.job_dedup import (
    JobDedupLLMService,
    build_job_dedup_service,
    dedupe_ordered_deterministic,
)
from scraper.base import RawJobData
from scraper.providers.jobspy_provider import JobSpyProvider

logger = get_logger(__name__)

DEFAULT_MARKET_COUNTRIES = ["Switzerland", "Luxembourg"]


@dataclass
class OrchestratorResult:
    jobs: list[RawJobData] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


class ScrapeOrchestrator:
    def __init__(
        self,
        *,
        hours_old: int = 24,
        limit_per_call: Optional[int] = None,
        countries: Optional[list[str]] = None,
    ):
        self.hours_old = hours_old
        self.limit_per_call = (
            limit_per_call if limit_per_call is not None else effective_scrape_limit()
        )
        self.countries = countries or DEFAULT_MARKET_COUNTRIES

    def _job_dedup(self, skip_llm: bool) -> Optional[JobDedupLLMService]:
        """规范化 + 标题向量漏斗；无 sentence-transformers 时退回 None（仅用第一步）。"""
        return build_job_dedup_service(skip_llm)

    def run_stages(self, *, skip_llm: bool = False) -> OrchestratorResult:
        out = OrchestratorResult()
        out.meta = {
            "hours_old": self.hours_old,
            "countries": self.countries,
            "skip_llm": skip_llm,
            "limit_per_call": self.limit_per_call,
        }

        dedup = self._job_dedup(skip_llm)

        # --- JobSpy：按地区 + 语言 + 关键词（core.search_keywords）抓取 ---
        provider = JobSpyProvider(
            site_names=["linkedin"],
            hours_old=self.hours_old,
            use_location_keywords=True,
            linkedin_fetch_description=False,
        )
        raw = provider.fetch_jobs(
            keywords=[],
            countries=self.countries,
            limit=self.limit_per_call,
        )
        out.meta["jobspy_fetched_raw"] = len(raw)
        logger.info(f"JobSpy 原始条数: {len(raw)}")

        if dedup:
            out.jobs, removed = dedup.dedupe_ordered_sequence(raw)
        else:
            out.jobs, removed = dedupe_ordered_deterministic(raw)
        out.meta["sequence_removed_indices"] = removed
        out.meta["jobs_after_dedup"] = len(out.jobs)
        logger.info(f"JobSpy 去重后: {len(out.jobs)}")

        return out
