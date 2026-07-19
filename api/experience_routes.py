"""
经历库（求职方向 / 确定性事实 / 经历单元）REST 路由：为后续大改前端留好接口。风格与
api/web_routes.py 一致——裸路由函数 + 内联 Pydantic model + `with get_db() as db:`。
每条路由都是 /users/{user_id}/...，鉴权（调用者必须是 user_id 本人或 admin）由
router 级 dependencies 统一处理，见 api/auth_deps.py::require_owner_or_admin。
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from api.auth_deps import require_owner_or_admin
from core.config import settings
from core.logger import get_logger
from ai.llm_client import LLMClient
from ai.llm_factory import get_scoring_llm_client
from db.models import UserCandidateFacts, UserExperienceUnit, UserJobDirection, UserJobScore
from db.session import get_db
from services.experience_library_service import ExperienceLibraryService
from services.notification_service import NotificationService
from services.scoring_service import JobScoringService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1", tags=["experience"], dependencies=[Depends(require_owner_or_admin)]
)


def _direction_payload(d: UserJobDirection) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "expanded_text": d.expanded_text,
        "is_active": d.is_active,
        "embed_model": d.embed_model,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _experience_unit_payload(u: UserExperienceUnit) -> dict:
    return {
        "id": u.id,
        "title": u.title,
        "employer": u.employer,
        "background": u.background,
        "actions": u.actions,
        "technologies": u.technologies,
        "ownership": u.ownership,
        "results": u.results,
        "domain": u.domain,
        "start_date": u.start_date.isoformat() if u.start_date else None,
        "end_date": u.end_date.isoformat() if u.end_date else None,
        "raw_date_text": u.raw_date_text,
        "raw_text": u.raw_text,
        "order_index": u.order_index,
        "tier": u.tier,
        "source": u.source,
        "confirmed": u.confirmed,
        "confirmed_at": u.confirmed_at.isoformat() if u.confirmed_at else None,
    }


def _candidate_facts_payload(f: UserCandidateFacts | None) -> dict:
    if not f:
        return {"atoms": [], "total_years_experience": None, "source": None, "confirmed": False, "confirmed_at": None}
    return {
        "atoms": f.atoms,
        "total_years_experience": f.total_years_experience,
        "source": f.source,
        "confirmed": f.confirmed,
        "confirmed_at": f.confirmed_at.isoformat() if f.confirmed_at else None,
    }


def _mid_llm() -> LLMClient:
    return get_scoring_llm_client(settings.scoring_model_mid)


# ---------------------------------------------------------------------------
# 求职方向
# ---------------------------------------------------------------------------

class DirectionCreateBody(BaseModel):
    title: str = Field(..., min_length=1)


class DirectionUpdateBody(BaseModel):
    title: str | None = None
    is_active: bool | None = None


@router.get("/users/{user_id}/directions")
def api_list_directions(user_id: int):
    with get_db() as db:
        rows = ExperienceLibraryService.list_directions(db, user_id)
        return [_direction_payload(d) for d in rows]


@router.post("/users/{user_id}/directions")
def api_create_direction(user_id: int, body: DirectionCreateBody):
    with get_db() as db:
        try:
            d = ExperienceLibraryService.create_direction(db, user_id, body.title)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _direction_payload(d)


@router.put("/users/{user_id}/directions/{direction_id}")
def api_update_direction(user_id: int, direction_id: int, body: DirectionUpdateBody):
    with get_db() as db:
        try:
            d = ExperienceLibraryService.update_direction(
                db, user_id, direction_id,
                title=body.title, is_active=body.is_active,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _direction_payload(d)


@router.delete("/users/{user_id}/directions/{direction_id}")
def api_delete_direction(user_id: int, direction_id: int):
    with get_db() as db:
        ExperienceLibraryService.delete_direction(db, user_id, direction_id)
        return {"ok": True}


# ---------------------------------------------------------------------------
# 确定性事实
# ---------------------------------------------------------------------------

class CandidateFactsBody(BaseModel):
    atoms: list[dict] = Field(default_factory=list)
    total_years_experience: float | None = None


@router.get("/users/{user_id}/candidate-facts")
def api_get_candidate_facts(user_id: int):
    with get_db() as db:
        return _candidate_facts_payload(ExperienceLibraryService.get_candidate_facts(db, user_id))


@router.put("/users/{user_id}/candidate-facts")
def api_put_candidate_facts(user_id: int, body: CandidateFactsBody):
    with get_db() as db:
        facts = ExperienceLibraryService.upsert_candidate_facts(
            db, user_id,
            atoms=body.atoms,
            total_years_experience=body.total_years_experience,
            source="manual",
            mark_confirmed=True,
        )
        return _candidate_facts_payload(facts)


# ---------------------------------------------------------------------------
# 经历单元
# ---------------------------------------------------------------------------

class ExperienceUnitBody(BaseModel):
    title: str | None = None
    employer: str | None = None
    background: str | None = None
    actions: str | None = None
    technologies: list[str] = Field(default_factory=list)
    ownership: str | None = None
    results: str | None = None
    domain: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    raw_date_text: str | None = None
    raw_text: str | None = None
    order_index: int = 0
    # 用户 onboarding 时一次性标注的含金量粗档："flagship"|"solid"|"filler"，
    # 见 core.constants.ExperienceTier / ai/resume_selection.py
    tier: str | None = None


@router.get("/users/{user_id}/experience-units")
def api_list_experience_units(user_id: int):
    with get_db() as db:
        rows = ExperienceLibraryService.list_experience_units(db, user_id)
        return [_experience_unit_payload(u) for u in rows]


@router.post("/users/{user_id}/experience-units")
def api_create_experience_unit(user_id: int, body: ExperienceUnitBody):
    with get_db() as db:
        u = ExperienceLibraryService.create_experience_unit(db, user_id, **body.model_dump())
        return _experience_unit_payload(u)


@router.put("/users/{user_id}/experience-units/{unit_id}")
def api_update_experience_unit(user_id: int, unit_id: int, body: ExperienceUnitBody):
    with get_db() as db:
        try:
            u = ExperienceLibraryService.update_experience_unit(
                db, user_id, unit_id, **body.model_dump(exclude_unset=True)
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return _experience_unit_payload(u)


@router.delete("/users/{user_id}/experience-units/{unit_id}")
def api_delete_experience_unit(user_id: int, unit_id: int):
    with get_db() as db:
        ExperienceLibraryService.delete_experience_unit(db, user_id, unit_id)
        return {"ok": True}


# ---------------------------------------------------------------------------
# Bootstrap：从已上传 Master CV 一次性抽取种子数据
# ---------------------------------------------------------------------------

@router.post("/users/{user_id}/experience-library/extract-from-master-cv")
def api_extract_from_master_cv(user_id: int):
    with get_db() as db:
        try:
            result = ExperienceLibraryService.extract_from_master_cv(db, user_id, _mid_llm())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return result


# ---------------------------------------------------------------------------
# 手动触发：「开始匹配」——立刻对该用户打一遍分。可以反复点，只处理该用户还没打过分的岗位。
#
# DeepSeek 没有 batch 接口，点了就得跑，不分高峰非高峰（那是自动批次的事，见
# tasks/score_tasks.py::run_deepseek_matching_batch）；但 Gemini 那一层（Step 2 JD 提取）
# 现在走 Batch API 异步处理（见 services/gemini_jd_batch_service.py），所以这里能打分的
# 永远只是"Step 1 通过 + 恰好已经被 Gemini 处理过"的岗位——新方向第一次点击很可能凑不够
# 上限，缺口会在这个用户下次点击、或者下一次自动批次时（Gemini batch 出结果之后）补上，
# 不做特殊的"续跑"记录。
#
# 用户第一次打分（此前一条 UserJobScore 都没有）限流到最多 500 条，避免全新方向一次性
# 把整个历史岗位库都塞进 DeepSeek；之后的点击不设这个上限（仍然受 admin 配额约束，见
# services/quota_service.py，在 score_new_jobs_for_user 内部检查）。
# ---------------------------------------------------------------------------

_FIRST_MATCH_MAX_JOBS = 500


def _start_job_search_sync(user_id: int) -> dict:
    with get_db() as db:
        is_first_match = (
            db.query(UserJobScore).filter(UserJobScore.user_id == user_id).count() == 0
        )
        max_jobs = _FIRST_MATCH_MAX_JOBS if is_first_match else None
        score_result = JobScoringService(db).score_new_jobs_for_user(user_id, max_jobs=max_jobs)
        notify_result = NotificationService(db).notify_new_high_score_jobs(user_id)
        return {
            "scoring": score_result,
            "notified": notify_result,
        }


@router.post("/users/{user_id}/experience-library/start-job-search")
async def api_start_job_search(user_id: int):
    return await run_in_threadpool(_start_job_search_sync, user_id)
