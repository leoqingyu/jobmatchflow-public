from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.scrape_params import effective_scrape_limit
from core.logger import get_logger
from core.exceptions import ScraperError
from db.models import Job, JobIngestionLog
from scraper.base import BaseScraperProvider, RawJobData
from scraper.normalizer import normalize_job
from scraper.dedup import compute_content_hash, compute_jd_fingerprint, make_dedup_key

logger = get_logger(__name__)

# 岗位有效期：从 date_posted（缺失时退回 created_at）起超过这么多天，一律标记失效，
# 不管发布方那边这条岗位是否还在（纯按时间判断，不回源站核实）。
JOB_EXPIRE_AFTER_DAYS = 30

# 分时段抓取任务查重时，回看数据库里最近这么多天的同国家岗位当"母库"。
# 与 JOB_EXPIRE_AFTER_DAYS 故意分开：现在各源分散在不同时段跑，同一岗位跨源、跨时段重复出现
# 的时间跨度可能比"岗位还算不算新鲜"更长，查重要比过期判定更保守（回看更久）。
DEDUP_LOOKBACK_DAYS = 60


def load_recent_jobs_as_raw(
    db: Session,
    countries: list[str],
    lookback_days: int = DEDUP_LOOKBACK_DAYS,
) -> list[RawJobData]:
    """
    供分时段抓取任务用：查最近 lookback_days 天内、这些国家已入库的岗位，转成 RawJobData
    列表，作为跨源漏斗去重（ai.job_dedup）的"母库"——因为各抓取源现在分散在不同时间点跑，
    不再共享同一次调用的内存态列表，只能回数据库找"最近同国家已经有什么"。

    只取去重需要的列（不带正文），避免大字段拖慢查询。
    """
    if not countries:
        return []
    cutoff = datetime.utcnow() - timedelta(days=lookback_days)
    rows = (
        db.query(
            Job.source,
            Job.external_job_id,
            Job.title,
            Job.company,
            Job.location,
            Job.country,
            Job.url,
            Job.date_posted,
            Job.description_clean,
            Job.description_raw,
        )
        .filter(Job.country.in_(countries))
        .filter(Job.created_at >= cutoff)
        .all()
    )
    return [
        RawJobData(
            source=r.source,
            external_job_id=r.external_job_id,
            title=r.title,
            company=r.company,
            location=r.location,
            country=r.country,
            url=r.url,
            description_raw=r.description_clean or r.description_raw,
            date_posted=r.date_posted,
        )
        for r in rows
    ]


class JobIngestionService:
    def __init__(self, db: Session):
        self.db = db

    def ingest(
        self,
        provider: BaseScraperProvider,
        keywords: list[str],
        countries: list[str],
        limit: Optional[int] = None,
    ) -> dict:
        """执行一次完整的抓取 + 标准化 + 去重 + 入库流程"""
        log = JobIngestionLog(
            source=provider.source_name,
            keyword=",".join(keywords),
            country=",".join(countries),
            started_at=datetime.utcnow(),
            status="running",
        )
        self.db.add(log)
        self.db.flush()

        try:
            eff_limit = effective_scrape_limit() if limit is None else limit
            raw_jobs = provider.fetch_jobs(
                keywords=keywords, countries=countries, limit=eff_limit
            )
            log.fetched_count = len(raw_jobs)

            inserted = 0
            duplicates = 0

            new_job_ids: list[int] = []
            for raw in raw_jobs:
                normalized = normalize_job(raw)
                jid = self._upsert_job(normalized)
                if jid is not None:
                    inserted += 1
                    new_job_ids.append(jid)
                else:
                    duplicates += 1

            log.inserted_count = inserted
            log.duplicate_count = duplicates
            log.status = "done"
            log.finished_at = datetime.utcnow()

            logger.info(f"入库完成: fetched={log.fetched_count} inserted={inserted} dup={duplicates}")
            return {
                "fetched": log.fetched_count,
                "inserted": inserted,
                "duplicates": duplicates,
                "new_job_ids": new_job_ids,
            }

        except Exception as e:
            log.status = "failed"
            log.error_message = str(e)
            log.finished_at = datetime.utcnow()
            raise ScraperError(f"抓取失败: {e}") from e

    def ingest_raw_jobs(
        self,
        jobs: list[RawJobData],
        *,
        log_source: str,
        countries: list[str],
        keyword_note: str = "broad",
    ) -> dict:
        """
        将已抓取好的 RawJobData 列表入库（用于多阶段编排合并结果）。
        每条仍会走 normalize + 库内去重。
        """
        country_str = ",".join(countries)[:100]
        log = JobIngestionLog(
            source=log_source,
            keyword=(keyword_note or "")[:255],
            country=country_str,
            started_at=datetime.utcnow(),
            status="running",
        )
        self.db.add(log)
        self.db.flush()

        try:
            log.fetched_count = len(jobs)
            inserted = 0
            duplicates = 0
            new_job_ids: list[int] = []
            for raw in jobs:
                normalized = normalize_job(raw)
                jid = self._upsert_job(normalized)
                if jid is not None:
                    inserted += 1
                    new_job_ids.append(jid)
                else:
                    duplicates += 1
            log.inserted_count = inserted
            log.duplicate_count = duplicates
            log.status = "done"
            log.finished_at = datetime.utcnow()
            logger.info(
                f"批量入库完成: source={log_source} fetched={log.fetched_count} "
                f"inserted={inserted} dup={duplicates}"
            )
            return {
                "fetched": log.fetched_count,
                "inserted": inserted,
                "duplicates": duplicates,
                "new_job_ids": new_job_ids,
            }
        except Exception as e:
            log.status = "failed"
            log.error_message = str(e)
            log.finished_at = datetime.utcnow()
            raise ScraperError(f"批量入库失败: {e}") from e

    def _upsert_job(self, raw: RawJobData) -> Optional[int]:
        """
        按 (source, external_job_id) 查找：已存在则在内容变化时刷新字段（标题/描述/地点等，
        不触碰 status——过期判定见 `expire_stale_jobs`，不因重新抓到而恢复 active），返回 None
        计为 duplicate；不存在则插入，返回新行 jobs.id。
        """
        content_hash = compute_content_hash(raw)
        jd_fingerprint = compute_jd_fingerprint(raw)

        existing = (
            self.db.query(Job)
            .filter(Job.source == raw.source, Job.external_job_id == raw.external_job_id)
            .first()
        )

        if existing:
            if existing.content_hash != content_hash or existing.jd_fingerprint != jd_fingerprint:
                existing.title = raw.title
                existing.company = raw.company
                existing.location = raw.location
                existing.country = raw.country
                existing.url = raw.url
                existing.description_raw = raw.description_raw
                existing.description_clean = getattr(raw, "description_clean", None)
                existing.date_posted = raw.date_posted
                existing.content_hash = content_hash
                existing.jd_fingerprint = jd_fingerprint
                # JD 正文变了，缓存的结构化结果（打分用）就该作废，下次打分自动重算
                existing.structured_requirements = None
                self.db.flush()
                logger.info(
                    "岗位内容有更新，已刷新: source=%s external_job_id=%s job_id=%s",
                    raw.source,
                    raw.external_job_id,
                    existing.id,
                )
            return None

        job = Job(
            source=raw.source,
            external_job_id=raw.external_job_id,
            title=raw.title,
            company=raw.company,
            location=raw.location,
            country=raw.country,
            url=raw.url,
            description_raw=raw.description_raw,
            description_clean=getattr(raw, "description_clean", None),
            date_posted=raw.date_posted,
            content_hash=content_hash,
            jd_fingerprint=jd_fingerprint,
            status="active",
        )
        self.db.add(job)
        self.db.flush()
        return job.id

    def expire_stale_jobs(self, max_age_days: int = JOB_EXPIRE_AFTER_DAYS) -> int:
        """
        岗位从 date_posted（缺失则退回 created_at）起超过 max_age_days 天，一律标记 status="expired"。
        纯按时间判断，不回源站核实是否真的下架；已是 expired 的不重复处理。
        """
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)
        updated = (
            self.db.query(Job)
            .filter(Job.status != "expired")
            .filter(func.coalesce(Job.date_posted, Job.created_at) < cutoff)
            .update({"status": "expired"}, synchronize_session=False)
        )
        self.db.flush()
        if updated:
            logger.info("岗位过期标记: 超过 %s 天，标记 expired 共 %s 条", max_age_days, updated)
        return updated
