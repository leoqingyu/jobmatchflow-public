"""将 RawJobData 序列化为可 JSON 化的 dict（调试接口用）"""

from scraper.base import RawJobData


def raw_job_to_dict(job: RawJobData) -> dict:
    return {
        "source": job.source,
        "external_job_id": job.external_job_id,
        "title": job.title,
        "company": job.company,
        "location": job.location,
        "country": job.country,
        "url": job.url,
        "description_raw": job.description_raw,
        "description_preview": (job.description_raw or "")[:400] or None,
        "date_posted": job.date_posted.isoformat() if job.date_posted else None,
        "extra": job.extra,
    }
