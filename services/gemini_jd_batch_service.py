"""JD 结构化提取（Step 2）批处理：走 Gemini Batch API，不再同步调用。

流程：某用户的 Step 1（向量初筛）通过、但岗位还没有 structured_requirements 时，
services/scoring_service.py::_ensure_structured_requirements 会把 Job.jd_extraction_queued_at
标个时间戳、这次先跳过。这里独立定时（见 tasks/scheduler.py，建议每 30 分钟）：
submit_pending_batch 把排队中的岗位打包提交一个 Gemini batch；poll_and_apply_batches 检查
在途 batch，出结果就写回 Job.structured_requirements，供下一次打分命中缓存分支。

Gemini Batch API 没有最低条数要求——折扣来自"接受异步处理"本身，不是走量，哪怕排队只有
一两条也直接提交，不用攒够一定数量再交（已跟 Google 官方文档确认）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ai.scoring import build_jd_requirements_prompt, normalize_jd_requirements
from core.config import settings
from core.logger import get_logger
from db.models import GeminiJdBatch, Job

logger = get_logger(__name__)

# 单批次最多打包多少条岗位——inline request 官方建议控制在 20MB 以内；JD 文本通常几 KB，
# 这个上限留足余量，也让每次轮询要处理的结果数量可控。
_MAX_JOBS_PER_BATCH = 200

_TERMINAL_STATES = {
    "JOB_STATE_SUCCEEDED",
    "JOB_STATE_FAILED",
    "JOB_STATE_CANCELLED",
    "JOB_STATE_EXPIRED",
}


def _get_genai_client():
    from google import genai

    return genai.Client(api_key=settings.gemini_api_key)


def submit_pending_batch(db: Session) -> str | None:
    """把排队中的岗位打包提交一个 Gemini batch；没有排队中的岗位则不提交，返回 None。"""
    pending_jobs = (
        db.query(Job)
        .filter(
            Job.jd_extraction_queued_at.isnot(None),
            Job.structured_requirements.is_(None),
            Job.jd_extraction_batch_id.is_(None),
        )
        .order_by(Job.jd_extraction_queued_at.asc())
        .limit(_MAX_JOBS_PER_BATCH)
        .all()
    )
    if not pending_jobs:
        return None

    inline_requests: list[dict[str, Any]] = []
    for job in pending_jobs:
        system_prompt, user_prompt = build_jd_requirements_prompt(job.description_clean or "")
        inline_requests.append(
            {
                "contents": [{"parts": [{"text": user_prompt}], "role": "user"}],
                "metadata": {"key": str(job.id)},
                "config": {
                    "temperature": 0.2,
                    "system_instruction": system_prompt,
                },
            }
        )

    client = _get_genai_client()
    batch_job = client.batches.create(
        model=settings.scoring_model_mid,
        src=inline_requests,
        config={"display_name": f"jd-extract-{len(inline_requests)}-jobs"},
    )

    record = GeminiJdBatch(batch_name=batch_job.name, status="submitted", job_count=len(pending_jobs))
    db.add(record)
    db.flush()
    for job in pending_jobs:
        job.jd_extraction_batch_id = record.id
    db.commit()

    logger.info("提交 Gemini JD 提取 batch=%s job_count=%s", batch_job.name, len(pending_jobs))
    return batch_job.name


def poll_and_apply_batches(db: Session) -> dict:
    """检查所有在途（status=submitted）的 batch；成功的写回结果，失败/过期的把岗位退回排队。"""
    client = _get_genai_client()
    in_flight = db.query(GeminiJdBatch).filter(GeminiJdBatch.status == "submitted").all()

    applied = 0
    requeued = 0
    still_running = 0

    for record in in_flight:
        try:
            batch_job = client.batches.get(name=record.batch_name)
        except Exception as e:
            logger.warning("查询 Gemini batch 状态失败 batch=%s: %s", record.batch_name, e)
            continue

        state = batch_job.state.name
        if state not in _TERMINAL_STATES:
            still_running += 1
            continue

        jobs = db.query(Job).filter(Job.jd_extraction_batch_id == record.id).all()
        jobs_by_id = {str(j.id): j for j in jobs}

        if state == "JOB_STATE_SUCCEEDED":
            for inline_response in batch_job.dest.inlined_responses or []:
                key = (inline_response.metadata or {}).get("key")
                job = jobs_by_id.get(key)
                if job is None:
                    continue
                if inline_response.error is not None:
                    logger.warning(
                        "Gemini batch 单条失败 batch=%s job_id=%s: %s",
                        record.batch_name, key, inline_response.error,
                    )
                    job.jd_extraction_batch_id = None  # 退回排队，下一批重试
                    requeued += 1
                    continue
                raw_text = (inline_response.response.text or "") if inline_response.response else ""
                try:
                    from ai.llm_client import parse_llm_json_output

                    parsed = parse_llm_json_output(raw_text, task_name="jd_requirements_extract_batch")
                    result = normalize_jd_requirements(parsed)
                except Exception as e:
                    logger.warning("Gemini batch 结果解析失败 job_id=%s: %s", key, e)
                    job.jd_extraction_batch_id = None
                    requeued += 1
                    continue
                job.structured_requirements = result
                job.domain = result.get("job_domain")
                job.employment_type = result.get("employment_type")
                applied += 1
            record.status = "succeeded"
        else:
            # failed / cancelled / expired：整批退回排队，下一次 submit_pending_batch 重新打包
            for job in jobs:
                job.jd_extraction_batch_id = None
                requeued += 1
            record.status = state.lower().removeprefix("job_state_")
            record.error_message = f"batch state={state}"

        from datetime import datetime

        record.completed_at = datetime.utcnow()
        db.commit()

    return {"applied": applied, "requeued": requeued, "still_running": still_running}
