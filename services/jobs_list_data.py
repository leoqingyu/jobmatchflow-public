"""Jobs List 数据查询：供 User API / Web 前端共用。"""

from __future__ import annotations

from sqlalchemy import func, or_, text
from sqlalchemy.orm import Session, aliased

from core.config import settings
from core.constants import AssetType, EMPLOYMENT_TYPE_REJECT_MODEL, PREFILTER_REJECT_MODEL
from db.models import ApplicationTracking, GeneratedAsset, Job, UserJobScore, UserMasterCV, UserSavedJob
from services.resume_generation_service import is_tailored_resume_json
from services.ingestion_service import JOB_EXPIRE_AFTER_DAYS


def effective_job_decision(
    score: float | None,
    stored_decision: str | None,
    score_breakdown: dict | None,
) -> str | None:
    """按当前阈值计算列表展示状态；向量过滤仍保持 dismissed。"""
    if score is None:
        return None
    if (score_breakdown or {}).get("reason") == "direction_vector_prefilter_reject":
        return "discard"
    if score >= settings.score_threshold_generate:
        return "generate"
    if score >= settings.score_threshold_review:
        return "review"
    return "discard"


def job_source_label(source: str | None) -> str:
    if not source:
        return "未知"
    s = str(source).strip().lower()
    if "linkedin" in s:
        return "LinkedIn"
    return str(source).strip()


def _requirement_match_rows(requirement_matches: list | None, req_by_id: dict) -> list[dict]:
    """把 UserJobScore.requirement_matches（只有 requirement_id/match_level/reason 等）跟
    Job.structured_requirements 里的原子要求（text/category）拼成前端要的展示结构。主分数
    和对比分数（见 fetch_jobs_list_rows 的 include_model_comparison）复用同一份拼装逻辑。"""
    out = []
    for rm in requirement_matches or []:
        if not isinstance(rm, dict):
            continue
        rid = rm.get("requirement_id")
        if rid is None:
            continue
        src = req_by_id.get(rid) or {}
        out.append(
            {
                "id": rid,
                "text": src.get("text"),
                "category": rm.get("category"),
                "importance": rm.get("importance"),
                "match_level": rm.get("match_level"),
                "reason": rm.get("reason"),
                "confidence": rm.get("confidence"),
            }
        )
    return out


def fetch_jobs_list_rows(
    db: Session, user_id: int, *, include_model_comparison: bool = False
) -> list[dict]:
    """
    Jobs List 列表 SQL（含 ``JOBS_LIST_DEBUG_SHOW_ALL``、分数与时间窗）。

    主分数只认三种 llm_model 之一（见 _join_score）：settings.scoring_model_match（当前生效的
    打分配置里 Step3 逐项匹配实际用的模型，见 services/scoring_service.py 的
    `llm_model=self.llm_match.model_name`），或者 PREFILTER_REJECT_MODEL /
    EMPLOYMENT_TYPE_REJECT_MODEL 这两个 Step0/1 硬过滤哨兵值（这两种拦截发生在 Step3 之前，
    根本不会有 match 模型那一行，之前只认 match 模型会导致被拦截的岗位在这里查不到对应
    UserJobScore，被误判成"还没打分"而不是"已经被拦截"）——早期打分模型对比测试（见
    scripts/rescore_for_comparison.py）会给同一个 (user, job) 多插入一行不同 llm_model 的
    UserJobScore，如果不锁定主分数来源，这里的 join 会把同一岗位重复展开成好几张卡片；
    固定按"当前配置"取 match 模型那一行还有个好处：改了 SCORING_MODEL_MATCH 之后无需改
    这里的代码，自动跟着新配置走。
    include_model_comparison=True 时（见 api/user_app.py，由 settings.jobs_list_model_comparison
    控制）额外查一遍 settings.deepseek_model_name 那一行，把分数/决策/理由/逐项匹配作为
    compare_* 字段附加上去，不影响主排序/主筛选逻辑，纯展示用。
    """
    debug_all = settings.jobs_list_debug_show_all or settings.jobs_list_diagnostics
    not_expired = func.coalesce(Job.date_posted, Job.created_at) >= func.now() - text(
        f"interval '{JOB_EXPIRE_AFTER_DAYS} days'"
    )
    RecCv = aliased(UserMasterCV)
    _cols = (
        Job.id,
        Job.title,
        Job.company,
        Job.country,
        Job.source,
        Job.date_posted,
        Job.created_at,
        Job.url,
        Job.description_clean,
        Job.description_raw,
        Job.structured_requirements,
        UserJobScore.score,
        UserJobScore.decision,
        UserJobScore.reason_summary,
        UserJobScore.recommended_cv_id,
        UserJobScore.score_breakdown,
        UserJobScore.requirement_matches,
        UserJobScore.hard_constraints_hit,
        ApplicationTracking.application_status,
        RecCv.cv_name,
    )
    _join_score = (
        (UserJobScore.job_id == Job.id)
        & (UserJobScore.user_id == user_id)
        & (
            UserJobScore.llm_model.in_(
                [settings.scoring_model_match, PREFILTER_REJECT_MODEL, EMPLOYMENT_TYPE_REJECT_MODEL]
            )
        )
    )
    saved_job_ids_subquery = (
        db.query(UserSavedJob.job_id).filter(UserSavedJob.user_id == user_id).scalar_subquery()
    )
    if debug_all:
        rows = (
            db.query(*_cols)
            .outerjoin(UserJobScore, _join_score)
            .outerjoin(
                ApplicationTracking,
                (ApplicationTracking.job_id == Job.id)
                & (ApplicationTracking.user_id == user_id),
            )
            .outerjoin(RecCv, RecCv.id == UserJobScore.recommended_cv_id)
            .filter(Job.status == "active")
            .filter(not_expired)
            .order_by(
                UserJobScore.score.desc().nullslast(),
                Job.created_at.desc().nullslast(),
            )
            .all()
        )
    else:
        rows = (
            db.query(*_cols)
            .join(UserJobScore, _join_score)
            .outerjoin(
                ApplicationTracking,
                (ApplicationTracking.job_id == Job.id)
                & (ApplicationTracking.user_id == user_id),
            )
            .outerjoin(RecCv, RecCv.id == UserJobScore.recommended_cv_id)
            .filter(Job.status == "active")
            .filter(not_expired)
            # 岗位真正的有效期判断是上面的 not_expired（JOB_EXPIRE_AFTER_DAYS=30，见
            # services/ingestion_service.py），这里只按 score 再筛一道：score>=30 才进主列表
            # （30-60 分之间是 dismissed，这是设计，不是 bug）。用户在 Dismissed 里手动
            # ★ Save 过的岗位（UserSavedJob）要能绕开这道分数线——那是"我知道分低，但我
            # 就是想留着"的明确信号，不该被悄悄拿掉，害前端 Saved 面板点进去却找不到对应
            # 的 JobRow（回退显示 visible[0]，显示了别的岗位）。
            .filter(or_(UserJobScore.score >= 30, Job.id.in_(saved_job_ids_subquery)))
            .order_by(UserJobScore.score.desc())
            .all()
        )

    out: list[dict] = []
    req_by_id_by_job: dict[int, dict] = {}
    for r in rows:
        (
            jid,
            title,
            company,
            country,
            source,
            date_posted,
            created_at,
            url,
            desc_clean,
            desc_raw,
            structured_requirements,
            score,
            decision,
            reason,
            recommended_cv_id,
            score_breakdown,
            requirement_matches,
            hard_constraints_hit,
            application_status,
            recommended_cv_name,
        ) = r
        jd = (desc_clean or desc_raw or "") or ""
        jd = str(jd).strip()
        effective_decision = effective_job_decision(score, decision, score_breakdown)

        req_by_id = {}
        for req in (structured_requirements or {}).get("requirements") or []:
            if isinstance(req, dict) and req.get("id"):
                req_by_id[req["id"]] = req
        req_by_id_by_job[int(jid)] = req_by_id
        merged_requirement_matches = _requirement_match_rows(requirement_matches, req_by_id)

        out.append(
            {
                "id": int(jid),
                "title": title or "",
                "company": company or "",
                "country": country or "",
                "source": source,
                "source_label": job_source_label(source),
                "date_posted": date_posted.isoformat() if date_posted else None,
                "created_at": created_at.isoformat() if created_at else None,
                "url": url,
                "description_clean": desc_clean,
                "description_raw": desc_raw,
                "has_jd": bool(jd and jd.lower() not in ("none", "nan")),
                "score": float(score) if score is not None else None,
                "decision": effective_decision,
                "reason": reason,
                "recommended_cv_id": int(recommended_cv_id)
                if recommended_cv_id is not None
                else None,
                "recommended_cv_name": recommended_cv_name,
                "in_application": application_status is not None,
                "vector_similarity": (
                    float((score_breakdown or {}).get("vector_similarity"))
                    if score_breakdown and (score_breakdown or {}).get("vector_similarity") is not None
                    else None
                ),
                "requirement_matches": merged_requirement_matches,
                "hard_constraints_hit": hard_constraints_hit or [],
                "job_seniority": (score_breakdown or {}).get("job_seniority"),
                "seniority_mismatch": bool((score_breakdown or {}).get("seniority_mismatch")),
                "cap_applied": bool((score_breakdown or {}).get("cap_applied")),
                "preference_bonus": (score_breakdown or {}).get("preference_bonus"),
                "preference_bonus_reason": (score_breakdown or {}).get("preference_bonus_reason"),
                "processing_status": (
                    "unscored"
                    if score is None
                    else "vector_filtered"
                    if effective_decision == "discard" and (score_breakdown or {}).get("reason") == "direction_vector_prefilter_reject"
                    else "employment_type_filtered"
                    if effective_decision == "discard" and (score_breakdown or {}).get("reason") == "employment_type_mismatch"
                    else "scored"
                ),
                "compare_model": None,
                "compare_score": None,
                "compare_decision": None,
                "compare_reason": None,
                "compare_requirement_matches": [],
            }
        )

    if include_model_comparison and out:
        compare_model_name = settings.deepseek_model_name
        compare_rows = (
            db.query(
                UserJobScore.job_id,
                UserJobScore.score,
                UserJobScore.decision,
                UserJobScore.reason_summary,
                UserJobScore.score_breakdown,
                UserJobScore.requirement_matches,
            )
            .filter(
                UserJobScore.user_id == user_id,
                UserJobScore.llm_model == compare_model_name,
                UserJobScore.job_id.in_([j["id"] for j in out]),
            )
            .all()
        )
        compare_by_job = {int(cr[0]): cr for cr in compare_rows}
        for job in out:
            cr = compare_by_job.get(job["id"])
            if cr is None:
                continue
            _, c_score, c_decision, c_reason, c_breakdown, c_matches = cr
            job["compare_model"] = compare_model_name
            job["compare_score"] = float(c_score) if c_score is not None else None
            job["compare_decision"] = effective_job_decision(c_score, c_decision, c_breakdown)
            job["compare_reason"] = c_reason
            job["compare_requirement_matches"] = _requirement_match_rows(
                c_matches, req_by_id_by_job.get(job["id"], {})
            )
    return out


def job_ids_with_cover_letter_assets(
    db: Session, user_id: int, job_ids: list[int]
) -> set[int]:
    if not job_ids:
        return set()
    q = (
        db.query(GeneratedAsset.job_id)
        .filter(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.asset_type == AssetType.MOTIVATION_LETTER.value,
            GeneratedAsset.job_id.in_(job_ids),
        )
        .all()
    )
    return {int(r[0]) for r in q}


def job_ids_with_resume_assets(db: Session, user_id: int, job_ids: list[int]) -> set[int]:
    """兼容旧名：现表示已生成求职信。"""
    return job_ids_with_cover_letter_assets(db, user_id, job_ids)


def map_job_tailored_resume_flags(
    db: Session, user_id: int, job_ids: list[int]
) -> dict[int, bool]:
    """岗位是否存在「定制 JSON 简历」资产（resume_json + is_tailored_resume_json）。"""
    if not job_ids:
        return {}
    rows = (
        db.query(GeneratedAsset.job_id, GeneratedAsset.content_json)
        .filter(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.job_id.in_(job_ids),
            GeneratedAsset.asset_type == AssetType.RESUME_JSON.value,
        )
        .all()
    )
    out: dict[int, bool] = {}
    for jid, cj in rows:
        if is_tailored_resume_json(cj):
            out[int(jid)] = True
    return out
