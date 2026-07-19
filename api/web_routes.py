"""Web SPA 专用 REST 路由。"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi import Path as PathParam
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response

from api.auth_deps import (
    clear_session_cookie,
    get_current_user,
    require_admin,
    require_owner_or_admin,
    set_session_cookie,
)
from core.exceptions import RenderError
from core.generation_policy import read_generation_policy, write_generation_policy
from core.security import (
    generate_verification_code,
    hash_password,
    hash_verification_code,
    verify_password,
)
from core.config import settings
from core.logger import get_logger
from db.session import get_db
from notifier.email_sender import send_verification_email
from services.profile_photo import save_profile_photo_jpeg
from services.profile_photo import has_profile_photo
from services.profile_photo import profile_photo_path
from services.profile_service import ProfileService
from services.user_service import UserService
from services.cv_material_library_service import regenerate_material_library_html
from services.master_cv_union_service import (
    regenerate_master_cv_union_from_resume_sources,
    save_master_cv_json,
)
from db.models import EmailVerification, UserProfile, User
from services.web_portal import (
    advance_tracking_status,
    dashboard_metrics,
    delete_tracking_record,
    export_asset_file_bytes,
    fetch_tracking_rows,
    funnel_metrics,
    tracking_metrics,
    master_cv_payload,
    master_cvs_bundle_payload,
    preview_asset_html,
    preview_asset_thumbnail,
    public_settings_payload,
    save_asset_content_json,
    search_profile_countries,
    snapshot_assets_list,
    tracking_download,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["web"])
# Every /users/{user_id}/... endpoint lives here — the ownership dependency runs
# once per request for the whole router, not per endpoint function.
user_router = APIRouter(
    prefix="/api/v1/users/{user_id}",
    tags=["web-user"],
    dependencies=[Depends(require_owner_or_admin)],
)


@router.get("/dashboard/metrics")
def api_dashboard_metrics():
    with get_db() as db:
        return dashboard_metrics(db)


@router.get("/settings/public")
def api_settings_public():
    return public_settings_payload()


@router.get("/settings/generation-policy")
def api_get_generation_policy():
    """
    生成策略占位配置（读写独立接口）。
    当前不参与覆盖简历/求职信生成用的模型（固定读 core/config.py 配置），仅供后续扩展。
    """
    return read_generation_policy()


@router.put("/settings/generation-policy", dependencies=[Depends(require_admin)])
def api_put_generation_policy(body: dict = Body(...)):
    """写入 secrets/generation_policy.json；结构自定，建议保留 version / rules 等字段便于后续演进。"""
    try:
        write_generation_policy(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


class SignupBody(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=8)
    name: str = Field(..., min_length=1)


@router.post("/auth/signup")
def api_signup(body: SignupBody):
    email = body.email.strip().lower()
    with get_db() as db:
        if db.query(User).filter(User.email == email).first():
            raise HTTPException(status_code=409, detail="Email already registered")
        user = User(
            email=email,
            name=body.name.strip(),
            status="active",
            role="user",
            password_hash=hash_password(body.password),
        )
        db.add(user)
        db.flush()
        # Free-tier defaults for every new signup — admin raises/removes these manually
        # once the user pays (no automatic subscription/billing yet), see
        # api_admin_put_user_quota below.
        db.add(UserProfile(
            user_id=user.id,
            max_matched_jobs=500,
            max_generated_resumes=10,
            max_generated_cover_letters=10,
        ))
        code = generate_verification_code()
        db.add(EmailVerification(
            user_id=user.id,
            token_hash=hash_verification_code(user.id, code),
            expires_at=datetime.utcnow() + timedelta(minutes=15),
        ))
        user_id = user.id
    send_verification_email(email, code)
    return {"ok": True, "user_id": user_id, "message": "Check your email for a verification code"}


class VerifyEmailBody(BaseModel):
    email: str = Field(..., min_length=3)
    code: str = Field(..., min_length=6, max_length=6)


@router.post("/auth/verify-email")
def api_verify_email(body: VerifyEmailBody):
    email = body.email.strip().lower()
    with get_db() as db:
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=400, detail="Invalid email or code")
        token_hash = hash_verification_code(user.id, body.code.strip())
        record = (
            db.query(EmailVerification)
            .filter(
                EmailVerification.user_id == user.id,
                EmailVerification.token_hash == token_hash,
                EmailVerification.consumed_at.is_(None),
            )
            .order_by(EmailVerification.id.desc())
            .first()
        )
        if not record or record.expires_at < datetime.utcnow():
            raise HTTPException(status_code=400, detail="Invalid or expired code")
        record.consumed_at = datetime.utcnow()
        user.email_verified_at = datetime.utcnow()
    return {"ok": True}


class LoginBody(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=1)


@router.post("/auth/login")
def api_login(body: LoginBody, response: Response):
    email = body.email.strip().lower()
    with get_db() as db:
        user = db.query(User).filter(User.email == email, User.status == "active").first()
        if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        if not user.email_verified_at:
            raise HTTPException(status_code=403, detail="Please verify your email before logging in")
        user.last_login_at = datetime.utcnow()
        result = {"id": user.id, "email": user.email, "name": user.name, "role": user.role}
    set_session_cookie(response, result["id"])
    return result


@router.post("/auth/logout")
def api_logout(response: Response):
    clear_session_cookie(response)
    return {"ok": True}


@router.get("/auth/me")
def api_me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "name": current_user.name,
        "role": current_user.role,
    }


@router.get("/admin/users", dependencies=[Depends(require_admin)])
def api_admin_users():
    with get_db() as db:
        users = UserService(db).list_all_for_admin()
        overridden = dict(
            db.query(UserProfile.user_id, UserProfile.quota_overridden_by_admin)
            .filter(UserProfile.user_id.in_([u.id for u in users]))
            .all()
        )
        return [
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "status": u.status,
                "role": u.role,
                "email_verified": u.email_verified_at is not None,
                "created_at": u.created_at,
                "last_login_at": u.last_login_at,
                "quota_overridden_by_admin": overridden.get(u.id, False),
            }
            for u in users
        ]


@router.get("/admin/overview", dependencies=[Depends(require_admin)])
def api_admin_overview():
    from services.admin_stats_service import overview_stats

    with get_db() as db:
        return overview_stats(db)


@router.get("/admin/users/{user_id}/stats", dependencies=[Depends(require_admin)])
def api_admin_user_stats(user_id: int = PathParam(..., ge=1)):
    from services.admin_stats_service import user_stats

    with get_db() as db:
        return user_stats(db, user_id)


@router.get("/admin/users/{user_id}/quota", dependencies=[Depends(require_admin)])
def api_admin_get_user_quota(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        return {
            "user_id": user_id,
            "max_matched_jobs": profile.max_matched_jobs if profile else None,
            "max_generated_resumes": profile.max_generated_resumes if profile else None,
            "max_generated_cover_letters": profile.max_generated_cover_letters if profile else None,
            "allowed_countries": list(profile.allowed_countries) if profile and profile.allowed_countries else [],
            "quota_overridden_by_admin": profile.quota_overridden_by_admin if profile else False,
        }


class AdminQuotaBody(BaseModel):
    max_matched_jobs: int | None = Field(None, ge=0)
    max_generated_resumes: int | None = Field(None, ge=0)
    max_generated_cover_letters: int | None = Field(None, ge=0)
    allowed_countries: list[str] = Field(default_factory=list)


@router.put("/admin/users/{user_id}/quota", dependencies=[Depends(require_admin)])
def api_admin_put_user_quota(
    user_id: int = PathParam(..., ge=1),
    body: AdminQuotaBody = Body(...),
):
    from core.job_markets import sanitize_user_countries

    with get_db() as db:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if not profile:
            raise HTTPException(status_code=404, detail="User profile not found")
        profile.max_matched_jobs = body.max_matched_jobs
        profile.max_generated_resumes = body.max_generated_resumes
        profile.max_generated_cover_letters = body.max_generated_cover_letters
        profile.allowed_countries = sanitize_user_countries(body.allowed_countries)
        # Any admin write marks this user as manually managed from now on — signup no
        # longer auto-assigns free-tier defaults to it, and the admin table can show at
        # a glance who's still on the free tier vs who's been manually adjusted (paid).
        profile.quota_overridden_by_admin = True
    return {"ok": True, "user_id": user_id}


class AccountPutBody(BaseModel):
    name: str = Field(..., min_length=1)
    email: str = Field(..., min_length=3)


@router.post("/users")
def api_create_user_retired():
    """Retired: users self-register via POST /auth/signup now."""
    raise HTTPException(status_code=410, detail="Use POST /api/v1/auth/signup instead")


@user_router.get("/account")
def api_get_account(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        u = UserService(db).get_by_id(user_id)
        return {"id": u.id, "name": u.name, "email": u.email,
                "has_profile_photo": has_profile_photo(user_id)}


@user_router.put("/account")
def api_put_account(user_id: int = PathParam(..., ge=1), body: AccountPutBody = Body(...)):
    with get_db() as db:
        u = UserService(db).get_by_id(user_id)
        u.name = body.name.strip()
        u.email = body.email.strip()
        db.flush()
        return {"id": u.id, "name": u.name, "email": u.email, "has_profile_photo": has_profile_photo(user_id)}


@user_router.delete("/account")
def api_delete_account(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        u = UserService(db).get_by_id(user_id)
        u.status = "deleted"
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        if profile:
            profile.is_active = False
    return {"ok": True}


class SearchProfileBody(BaseModel):
    countries: list[str] = Field(default_factory=list)


@user_router.get("/search-profile")
def api_get_search_profile(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        allowed = list(profile.allowed_countries) if profile and profile.allowed_countries else []
        return {
            "user_id": user_id,
            "countries": search_profile_countries(db, user_id),
            "allowed_countries": allowed,
            "country_locked": len(allowed) <= 1,
        }


class ChooseCountryBody(BaseModel):
    country: str = Field(..., min_length=1)


@user_router.post("/onboarding/choose-country")
def api_choose_country(user_id: int = PathParam(..., ge=1), body: ChooseCountryBody = Body(...)):
    with get_db() as db:
        try:
            ProfileService(db).choose_locked_country(user_id, body.country)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


@user_router.put("/search-profile")
def api_put_search_profile(
    user_id: int = PathParam(..., ge=1),
    body: SearchProfileBody = SearchProfileBody(),
):
    with get_db() as db:
        ProfileService(db).upsert_default_search_profile_countries(
            user_id, body.countries
        )
    return {"ok": True, "user_id": user_id}


class ScoringPreferencesBody(BaseModel):
    scoring_preferences: str = Field(
        default="",
        max_length=300,
        description="用户补充的求职/岗位偏好，并入评分上下文；最长 300 字",
    )


@user_router.get("/scoring-preferences")
def api_get_scoring_preferences(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        text = (prof.scoring_preferences_text or "").strip() if prof else ""
    return {"user_id": user_id, "scoring_preferences": text}


@user_router.put("/scoring-preferences")
def api_put_scoring_preferences(
    user_id: int = PathParam(..., ge=1),
    body: ScoringPreferencesBody = Body(...),
):
    try:
        with get_db() as db:
            ProfileService(db).set_scoring_preferences_text(
                user_id, body.scoring_preferences
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


class EmploymentTypePreferenceBody(BaseModel):
    employment_type_preference: str = Field(
        ...,
        description='one of "internship_only" | "full_time_only" | "both"',
    )


@user_router.get("/employment-type-preference")
def api_get_employment_type_preference(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        preference = (prof.employment_type_preference if prof else None) or "both"
    return {"user_id": user_id, "employment_type_preference": preference}


@user_router.put("/employment-type-preference")
def api_put_employment_type_preference(
    user_id: int = PathParam(..., ge=1),
    body: EmploymentTypePreferenceBody = Body(...),
):
    try:
        with get_db() as db:
            ProfileService(db).set_employment_type_preference(
                user_id, body.employment_type_preference
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


class GenerationModelBody(BaseModel):
    generation_model: str = Field(..., description='one of "gemini" | "claude"')


@user_router.get("/generation-model")
def api_get_generation_model(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        model = ProfileService(db).get_generation_model(user_id) or "gemini"
    return {"user_id": user_id, "generation_model": model}


@user_router.put("/generation-model")
def api_put_generation_model(
    user_id: int = PathParam(..., ge=1),
    body: GenerationModelBody = Body(...),
):
    try:
        with get_db() as db:
            ProfileService(db).set_generation_model(user_id, body.generation_model)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


class ResumeTailoringModeBody(BaseModel):
    resume_tailoring_mode: str = Field(..., description='one of "honest" | "jd_aligned"')


@user_router.get("/resume-tailoring-mode")
def api_get_resume_tailoring_mode(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        mode = ProfileService(db).get_resume_tailoring_mode(user_id) or "honest"
    return {"user_id": user_id, "resume_tailoring_mode": mode}


@user_router.put("/resume-tailoring-mode")
def api_put_resume_tailoring_mode(
    user_id: int = PathParam(..., ge=1),
    body: ResumeTailoringModeBody = Body(...),
):
    try:
        with get_db() as db:
            ProfileService(db).set_resume_tailoring_mode(user_id, body.resume_tailoring_mode)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


@user_router.get("/master-cv")
def api_get_master_cv(
    user_id: int = PathParam(..., ge=1),
    include_full: bool = Query(False, description="true 时返回完整母简历正文"),
):
    with get_db() as db:
        return {
            "user_id": user_id,
            **master_cv_payload(db, user_id, include_full=include_full),
        }


class MasterCvPutBody(BaseModel):
    cv_name: str = Field(default="Master CV", min_length=1)
    text: str = Field(..., min_length=1)


@user_router.put("/master-cv")
def api_put_master_cv(
    user_id: int = PathParam(..., ge=1),
    body: MasterCvPutBody = Body(...),
):
    """兼容旧客户端：仍更新 id 最小的一条简历，不删其它条目。"""
    try:
        with get_db() as db:
            ProfileService(db).upsert_default_master_file(
                user_id=user_id,
                cv_name=body.cv_name.strip(),
                text=body.text,
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


@user_router.get("/resume-slots")
def api_list_resume_slots(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        return master_cvs_bundle_payload(db, user_id)


@user_router.post("/resume-slots")
def api_add_resume_slot_text_no_longer_supported(user_id: int = PathParam(..., ge=1)):
    raise HTTPException(
        status_code=400,
        detail="简历库仅支持 PDF：请使用 POST /users/{id}/resume-slots/upload 上传 PDF",
    )


@user_router.delete("/resume-slots/{cv_id}")
def api_delete_resume_slot(
    user_id: int = PathParam(..., ge=1),
    cv_id: int = PathParam(..., ge=1),
):
    try:
        with get_db() as db:
            ProfileService(db).delete_master_cv(user_id, cv_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


@user_router.post("/resume-material-library/rebuild")
def api_rebuild_resume_material_library(user_id: int = PathParam(..., ge=1)):
    """兼容旧路径：等同从 PDF 生成 Master CV HTML。"""
    try:
        with get_db() as db:
            html = regenerate_material_library_html(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "char_count": len(html)}


@user_router.post("/master-cv/rebuild-from-pdfs")
def api_rebuild_master_cv_from_pdfs(user_id: int = PathParam(..., ge=1)):
    try:
        with get_db() as db:
            _j, html = regenerate_master_cv_union_from_resume_sources(db, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "char_count": len(html)}


@user_router.get("/master-cv/preview-html")
def api_master_cv_preview_html(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        html = (prof.cv_material_library_html or "").strip() if prof else ""
    if not html:
        raise HTTPException(
            status_code=404,
            detail="尚未生成 Master CV：请先在 Settings 从 PDF 生成，或调用 rebuild-from-pdfs",
        )
    return Response(content=html, media_type="text/html; charset=utf-8")


@user_router.get("/master-cv/json")
def api_get_master_cv_json(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        j = prof.master_cv_json if prof else None
    return {"user_id": user_id, "master_cv_json": j or {}}


class MasterCvJsonPutBody(BaseModel):
    master_cv_json: dict = Field(default_factory=dict)


@user_router.put("/master-cv/json")
def api_put_master_cv_json(
    user_id: int = PathParam(..., ge=1),
    body: MasterCvJsonPutBody = Body(...),
):
    try:
        with get_db() as db:
            j, html = save_master_cv_json(db, user_id, body.master_cv_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "html_char_count": len(html), "json_keys": len(j.keys())}


@user_router.post("/resume-slots/upload")
async def api_upload_resume_slot_file(
    user_id: int = PathParam(..., ge=1),
    file: UploadFile = File(...),
    cv_name: str | None = Query(None, description="展示名称，默认取文件名"),
):
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if not raw.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件（内容应以 %PDF 开头）")
    fn = (file.filename or "").strip().lower()
    if fn and not fn.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="文件名应为 .pdf")
    name = (cv_name or file.filename or "Resume").strip() or "Resume"
    try:
        with get_db() as db:
            cv = ProfileService(db).add_resume_pdf_slot(
                user_id,
                name,
                raw,
                file.filename or "cv.pdf",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "cv_id": cv.id}


@user_router.post("/resume-slots/{slot_number}/upload")
async def api_upload_resume_slot_by_number(
    user_id: int = PathParam(..., ge=1),
    slot_number: int = PathParam(..., ge=1, le=2),
    file: UploadFile = File(...),
    cv_name: str | None = Query(None, description="展示名称，默认取文件名"),
):
    """固定 2 槽位简历上传（Settings 页「简历1/简历2」）：槽位已有内容时原地覆盖。"""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="空文件")
    if not raw.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="请上传 PDF 文件（内容应以 %PDF 开头）")
    fn = (file.filename or "").strip().lower()
    if fn and not fn.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="文件名应为 .pdf")
    name = (cv_name or file.filename or f"Resume {slot_number}").strip() or f"Resume {slot_number}"
    try:
        with get_db() as db:
            cv = ProfileService(db).upsert_resume_pdf_slot(
                user_id,
                slot_number,
                name,
                raw,
                file.filename or "cv.pdf",
            )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "cv_id": cv.id, "slot_number": slot_number}


@user_router.get("/resume-slots/{cv_id}/download")
def api_download_resume_slot_file(
    user_id: int = PathParam(..., ge=1),
    cv_id: int = PathParam(..., ge=1),
):
    from pathlib import Path as P

    with get_db() as db:
        from db.models import UserMasterCV

        row = db.get(UserMasterCV, cv_id)
        if not row or row.user_id != user_id:
            raise HTTPException(status_code=404, detail="简历不存在")
        fp = (row.source_file_path or "").strip()
        if fp and P(fp).is_file():
            return FileResponse(fp, filename=P(fp).name)
        body = (row.cv_markdown or "").encode("utf-8")
        safe = "".join(
            c if c.isalnum() or c in "._-" else "_" for c in (row.cv_name or "cv")[:80]
        )
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{safe}.txt"',
            },
        )


@user_router.post("/profile-photo")
async def api_post_profile_photo(
    user_id: int = PathParam(..., ge=1),
    file: UploadFile = File(...),
):
    raw = await file.read()
    try:
        save_profile_photo_jpeg(user_id, raw)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "user_id": user_id}


@user_router.get("/profile-photo")
def api_get_profile_photo(user_id: int = PathParam(..., ge=1)):
    """上传接口只落盘，一直没有对应的读取接口——前端拿不到图直接显示，看起来像"没保存"。"""
    path = profile_photo_path(user_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="no profile photo")
    return FileResponse(path, media_type="image/jpeg")


class PipelineRunBody(BaseModel):
    trigger_user_id: int = Field(..., ge=1)


def _run_pipeline_sync(uid: int):
    from tasks.fetch_tasks import run_full_pipeline_task

    return run_full_pipeline_task(uid)


@router.post("/pipeline/run", dependencies=[Depends(require_admin)])
async def api_run_pipeline(body: PipelineRunBody):
    try:
        result = await run_in_threadpool(_run_pipeline_sync, body.trigger_user_id)
    except Exception as e:
        logger.exception(
            "pipeline/run 执行失败 trigger_user_id=%s: %s",
            body.trigger_user_id,
            e,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e
    try:
        safe = jsonable_encoder(result)
    except Exception as e:
        logger.exception(
            "pipeline/run 结果无法序列化为 JSON trigger_user_id=%s: %s",
            body.trigger_user_id,
            e,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Pipeline 已执行完，但返回结果无法序列化为 JSON（请查服务端日志）: {e}",
        ) from e
    return {"ok": True, "result": safe}


@user_router.get("/tracking")
def api_tracking_list(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        rows, by_job = fetch_tracking_rows(db, user_id)
    assets_meta: dict[str, dict] = {}
    for jid, amap in by_job.items():
        assets_meta[str(jid)] = {
            k: {"id": v["id"], "file_path": v["file_path"]}
            for k, v in amap.items()
        }
    with get_db() as db:
        metrics = tracking_metrics(db, user_id)
        funnel = funnel_metrics(db, user_id)
    return {"user_id": user_id, "rows": rows, "assets_by_job_id": assets_meta, "metrics": metrics, "funnel": funnel}


class TrackingStageBody(BaseModel):
    stage: str = Field(..., description="interview | offer | rejected — 单向不可回溯，见 core.constants.ApplicationStatus")


@user_router.post("/tracking/jobs/{job_id}/stage")
def api_tracking_stage(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
    body: TrackingStageBody = Body(...),
):
    try:
        with get_db() as db:
            new_val = advance_tracking_status(db, user_id, job_id, body.stage)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "application_status": new_val}


@user_router.delete("/tracking/jobs/{job_id}")
def api_tracking_delete(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
):
    """撤销投递：岗位回到 Jobs 列表里"未投递"状态（不再变灰），跟状态回溯（不允许）是两回事。"""
    with get_db() as db:
        delete_tracking_record(db, user_id, job_id)
    return {"ok": True}


@user_router.get("/jobs/{job_id}/downloads")
def api_tracking_download(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
    kind: str = Query(..., description="jd_txt | resume_docx | letter_docx"),
):
    try:
        with get_db() as db:
            data, filename, mime = tracking_download(db, user_id, job_id, kind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@user_router.get("/assets")
def api_assets_list(user_id: int = PathParam(..., ge=1)):
    with get_db() as db:
        payload = snapshot_assets_list(db, user_id)
        return {"user_id": user_id, **payload}


@user_router.get("/assets/{asset_id}/preview-html")
def api_asset_preview_html(
    user_id: int = PathParam(..., ge=1),
    asset_id: int = PathParam(..., ge=1),
):
    """简历/求职信是 DOCX 单模板，没有 HTML 中间态可预览——这两类资产此接口恒 404，
    保留只是不破坏路由存在性；真正的预览留给后续前端改版直接展示 content_json 或提供下载。"""
    try:
        with get_db() as db:
            html = preview_asset_html(db, user_id, asset_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not html.strip():
        raise HTTPException(status_code=404, detail="暂无预览 HTML")
    return Response(content=html, media_type="text/html; charset=utf-8")


class AssetContentBody(BaseModel):
    content_json: dict = Field(default_factory=dict)


@user_router.put("/assets/{asset_id}/content")
def api_asset_put_content(
    user_id: int = PathParam(..., ge=1),
    asset_id: int = PathParam(..., ge=1),
    body: AssetContentBody = Body(...),
):
    """编辑后立即重新渲染 DOCX 并覆盖旧文件（不再是"编辑存 JSON，另外单独导出"两步）。"""
    try:
        with get_db() as db:
            save_asset_content_json(db, user_id, asset_id, body.content_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RenderError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True}


@user_router.post("/assets/{asset_id}/preview-thumbnail")
def api_asset_preview_thumbnail(
    user_id: int = PathParam(..., ge=1),
    asset_id: int = PathParam(..., ge=1),
    body: AssetContentBody = Body(...),
):
    """
    编辑页面实时预览：不落库，把当前草稿（未必已保存）渲染成缩略图。LibreOffice/PyMuPDF
    不可用时 thumbnail_png_base64 为 null，前端按"预览暂不可用"处理，不阻断正常编辑/保存。
    """
    try:
        with get_db() as db:
            return preview_asset_thumbnail(db, user_id, asset_id, body.content_json)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@user_router.post("/assets/{asset_id}/export-file")
def api_asset_export_file(
    user_id: int = PathParam(..., ge=1),
    asset_id: int = PathParam(..., ge=1),
):
    try:
        with get_db() as db:
            data, filename = export_asset_file_bytes(db, user_id, asset_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
