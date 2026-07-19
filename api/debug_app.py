"""
本地调试 HTTP 接口：只跑抓取/编排逻辑，默认不写数据库。

启动（在 jobmatchflow 项目根目录）：
  uvicorn api.debug_app:app --reload --port 8765

或：
  python -m uvicorn api.debug_app:app --reload --port 8765
"""

from __future__ import annotations

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from core.exceptions import ScraperError
from pydantic import BaseModel, ConfigDict, Field

from core.config import settings
from core.scrape_params import (
    effective_scrape_hours,
    effective_scrape_limit,
    resolve_scrape_params,
)
from scraper.providers.jobspy_provider import JobSpyProvider
from scraper.serialization import raw_job_to_dict
from services.pre_db_pipeline import jobs_ready_for_ingestion, run_pre_db_pipeline
from services.scrape_orchestrator import DEFAULT_MARKET_COUNTRIES, OrchestratorResult

app = FastAPI(title="JobMatchFlow Debug API", version="0.1")


@app.exception_handler(ScraperError)
async def scraper_error_handler(_request: Request, exc: ScraperError) -> JSONResponse:
    msg = exc.args[0] if exc.args else "抓取参数错误"
    return JSONResponse(status_code=422, content={"detail": msg})


@app.get("/", include_in_schema=False)
def root():
    """浏览器打开根路径时跳到 Swagger；仍可用 GET /about 看纯 JSON 说明。"""
    return RedirectResponse(url="/docs", status_code=307)


@app.get("/about", tags=["meta"])
def about():
    return {
        "service": "JobMatchFlow Debug API",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "endpoints": {
            "GET /health": "健康检查",
            "POST /debug/scrape/raw": "【仅 JobSpy】按 body.sites 抓一页；无编排、无 Gemini 去重",
            "POST /debug/pipeline/dry-run": "【完整入库前链】JobSpy 抓取 + 漏斗去重（规范化→本地向量→灰区 Gemini）；与生产一致，不入库",
        },
        "note": "若设置了 DEBUG_API_TOKEN，上述 POST 需 Header: Authorization: Bearer <token>",
        "pipeline": "run_pre_db_pipeline → jobs_ready_for_ingestion（入库前规范化+向量去重、无 LLM）→ ingest；编排阶段 skip_llm 仅跳过灰区 LLM，向量仍可用",
        "which_endpoint": "要测 Gemini 去重，请用 dry-run，不要用 scrape/raw",
        "scrape_defaults_from_env": {
            "hours_old_effective": effective_scrape_hours(),
            "limit_per_search_effective": effective_scrape_limit(),
            "SCRAPE_HOURS_OLD_raw": settings.scrape_hours_old,
            "SCRAPE_LIMIT_PER_SEARCH_raw": settings.scrape_limit_per_search,
            "note": "Debug 请求里 hours_old/limit 传 null 时与上述 effective 一致；见 core.scrape_params",
        },
    }


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """避免浏览器请求图标时刷 404 日志。"""
    return Response(status_code=204)


def _check_debug_token(authorization: Optional[str]) -> None:
    token = settings.debug_api_token or ""
    if not token:
        return
    expected = f"Bearer {token}"
    if (authorization or "").strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing debug API token")


def _serialize_result(r: OrchestratorResult) -> dict[str, Any]:
    dedup_keys = [k for k in r.meta if "removed" in k or k == "llm_dedup_skipped"]
    return {
        "pre_db": {
            "merged_count_if_ingested": len(jobs_ready_for_ingestion(r)),
            "llm_dedup_skipped": r.meta.get("llm_dedup_skipped", r.meta.get("skip_llm")),
            "dedup_related_meta_keys": dedup_keys,
        },
        "meta": r.meta,
        "counts": {"jobs": len(r.jobs)},
        "jobs": [raw_job_to_dict(j) for j in r.jobs],
    }


@app.get("/health")
def health():
    return {"ok": True, "env": settings.environment}


class RawScrapeBody(BaseModel):
    """
    单次 JobSpy 探针：只调 python-jobspy，不包含 ScrapeOrchestrator / pre_db_pipeline。
    不会调用 Gemini 去重。
    """

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "sites": ["linkedin"],
                    "countries": ["Switzerland", "Luxembourg"],
                    "hours_old": None,
                    "limit": None,
                    "broad_keyword": True,
                    "linkedin_fetch_description": False,
                    "keywords": [],
                    "use_location_keywords": False,
                    "keyword_domains": None,
                    "canton_codes": None,
                }
            ]
        }
    )

    sites: list[str] = Field(default=["linkedin"])
    countries: list[str] = Field(
        default_factory=lambda: list(DEFAULT_MARKET_COUNTRIES),
        description="须为 JobSpy 支持的国家英文名，如 Switzerland、Luxembourg，勿使用 Swagger 占位 string",
    )
    hours_old: Optional[int] = Field(
        default=None,
        description="null=使用 .env SCRAPE_HOURS_OLD（未设则有效 24h）；整数=仅本次覆盖",
    )
    limit: Optional[int] = Field(
        default=None,
        description="null=使用 .env SCRAPE_LIMIT_PER_SEARCH；整数=仅本次覆盖",
    )
    broad_keyword: bool = True
    linkedin_fetch_description: bool = False
    keywords: list[str] = Field(default_factory=list)
    use_location_keywords: bool = Field(
        default=False,
        description="True 时忽略 keywords/broad_keyword，改为按州/国家的语言关键词表（core.search_keywords）抓取，与生产抓取阶段一致",
    )
    keyword_domains: Optional[list[str]] = Field(
        default=None,
        description="仅在 use_location_keywords=True 时生效；子集如 ['tech','finance']，null=默认全部（tech/finance/cross）",
    )
    canton_codes: Optional[list[str]] = Field(
        default=None,
        description="仅瑞士生效：只抓这些州代码，如 ['ZH','ZG','GE','VD','BS','BE']；null=26 州全抓",
    )


@app.post("/debug/scrape/raw", tags=["debug: 仅 JobSpy"])
def debug_scrape_raw(
    body: RawScrapeBody,
    authorization: Optional[str] = Header(default=None),
):
    """
    单次 JobSpy 抓取，不入库。

    **与生产「入库前」流水线无关**：无编排、无 Gemini 去重。
    需要完整编排 + 去重时，请调用 **`POST /debug/pipeline/dry-run`**。
    """
    _check_debug_token(authorization)
    h, lim = resolve_scrape_params(body.hours_old, body.limit)
    provider = JobSpyProvider(
        site_names=body.sites,
        hours_old=h,
        broad_keyword=body.broad_keyword,
        linkedin_fetch_description=body.linkedin_fetch_description,
        use_location_keywords=body.use_location_keywords,
        keyword_domains=body.keyword_domains,
        canton_codes=body.canton_codes,
    )
    jobs = provider.fetch_jobs(
        keywords=body.keywords,
        countries=body.countries,
        limit=lim,
    )
    return {
        "_mode": "jobspy_raw_only",
        "_hint": "完整流水线（JobSpy 抓取 + Gemini 去重）请用 POST /debug/pipeline/dry-run",
        "count": len(jobs),
        "resolved_hours_old": h,
        "resolved_limit": lim,
        "sites": body.sites,
        "countries": body.countries,
        "jobs": [raw_job_to_dict(j) for j in jobs],
    }


class PipelineDryRunBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "hours_old": None,
                    "limit_per_call": None,
                    "countries": ["Switzerland", "Luxembourg"],
                    "skip_llm": False,
                }
            ]
        }
    )

    hours_old: Optional[int] = Field(
        default=None,
        description="null=使用 .env SCRAPE_HOURS_OLD（未设则有效 24h）；整数=仅本次覆盖",
    )
    limit_per_call: Optional[int] = Field(
        default=None,
        description="null=使用 .env SCRAPE_LIMIT_PER_SEARCH",
    )
    countries: list[str] = Field(
        default_factory=lambda: list(DEFAULT_MARKET_COUNTRIES),
        description="如 Switzerland、Luxembourg；不要用 [\"string\"]",
    )
    skip_llm: bool = Field(
        default=False,
        description="生产为 False。仅在没有 GEMINI_API_KEY、只想测抓取链时改为 True",
    )


@app.post("/debug/pipeline/dry-run", tags=["debug: 入库前流水线"])
def debug_pipeline_dry_run(
    body: PipelineDryRunBody,
    authorization: Optional[str] = Header(default=None),
):
    """
    与 `run_pre_db_pipeline` + `jobs_ready_for_ingestion` 一致（入库前整条链），不写库。
    默认启用灰区 LLM（有 GEMINI_API_KEY 时）；响应中 `jobs` 经漏斗去重；`merged_count_if_ingested` 含入库前「规范化+向量（无 LLM）」去重后的条数。
    """
    _check_debug_token(authorization)
    result = run_pre_db_pipeline(
        countries=body.countries,
        hours_old=body.hours_old,
        limit_per_call=body.limit_per_call,
        skip_llm=body.skip_llm,
    )
    return _serialize_result(result)
