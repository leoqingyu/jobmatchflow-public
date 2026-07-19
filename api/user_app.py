"""
JobMatchFlow 用户侧 HTTP API（多份上传简历 + 素材库评分推荐 + 求职信生成等）。

简历/求职信各自的生成路由在 api/resume_routes.py（同步函数，FastAPI 自动丢进线程池执行，
不阻塞 ASGI 事件循环），本文件只挂载路由 + 少数轻量端点（mark-applied、save/unsave 等）。

启动（项目根目录）::

  uvicorn api.user_app:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from starlette.concurrency import run_in_threadpool
from starlette.responses import RedirectResponse, Response

from api.auth_deps import require_owner_or_admin
from api.web_routes import router as web_portal_router, user_router as web_portal_user_router
from api.experience_routes import router as experience_router
from api.resume_routes import router as resume_router
from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)

_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
HAS_FRONTEND_SPA = _FRONTEND_DIST.is_dir()

if settings.environment == "production" and not settings.secret_key:
    raise RuntimeError(
        "SECRET_KEY must be set in production (see .env.example) — refusing to "
        "start with an empty JWT signing key."
    )

app = FastAPI(
    title="JobMatchFlow User API",
    version="0.1.0",
)

_cors_raw = (settings.api_cors_origins or "").strip()
if _cors_raw and _cors_raw != "*":
    _cors_list = [o.strip() for o in _cors_raw.split(",") if o.strip()]
    _cors_cred = True
else:
    _cors_list = ["*"]
    _cors_cred = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_list,
    allow_credentials=_cors_cred,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(web_portal_router)
app.include_router(web_portal_user_router)
app.include_router(experience_router)
app.include_router(resume_router)


@app.on_event("startup")
def _bootstrap_admin() -> None:
    """Create the first admin account if none exists yet. Not a login path itself —
    the admin logs in afterwards through the normal /auth/login with this
    email/password, stored as a real bcrypt hash like any other user."""
    if not settings.admin_bootstrap_password:
        return

    from datetime import datetime

    from db.session import get_db
    from db.models import User, UserProfile
    from core.security import hash_password

    with get_db() as db:
        if db.query(User).filter(User.role == "admin").first():
            return
        email = settings.admin_bootstrap_email.strip().lower()
        user = db.query(User).filter(User.email == email).first()
        if user:
            user.role = "admin"
            user.password_hash = hash_password(settings.admin_bootstrap_password)
            user.email_verified_at = user.email_verified_at or datetime.utcnow()
        else:
            user = User(
                email=email,
                name="Administrator",
                status="active",
                role="admin",
                password_hash=hash_password(settings.admin_bootstrap_password),
                email_verified_at=datetime.utcnow(),
            )
            db.add(user)
            db.flush()
            db.add(UserProfile(user_id=user.id))
        logger.info("Bootstrapped admin account: %s", email)


@app.middleware("http")
async def _spa_entry_no_cache(request: Request, call_next):
    """
    避免浏览器长期缓存 /app/ 下的 index.html，否则 rebuild 后仍指向旧 hash 的 JS。
    （带 hash 的 /app/assets/* 仍可被长期缓存。）
    """
    response = await call_next(request)
    path = request.url.path
    if path in ("/app", "/app/") or path.endswith("/index.html"):
        ct = (response.headers.get("content-type") or "").lower()
        if "text/html" in ct:
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
    return response


def _mark_applied_sync(user_id: int, job_id: int, resume_choice: str) -> None:
    from db.session import get_db
    from services.tracking_service import ApplicationTrackingService

    with get_db() as db:
        ApplicationTrackingService(db).mark_applied(user_id, job_id, resume_choice=resume_choice)


@app.get("/", include_in_schema=False)
def root():
    """有前端构建产物时进 Web 应用，否则进 Swagger。"""
    if HAS_FRONTEND_SPA:
        return RedirectResponse(url="/app/", status_code=307)
    return RedirectResponse(url="/docs", status_code=307)


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health():
    return {"status": "ok", "service": "jobmatchflow-user-api"}


@app.get("/api/v1/users/{user_id}/profile", dependencies=[Depends(require_owner_or_admin)])
def user_profile_flags(user_id: int = PathParam(..., ge=1)):
    """是否已上传证件照（求职信版式等会读取）。"""
    from services.profile_photo import has_profile_photo

    return {
        "user_id": user_id,
        "has_profile_photo": bool(has_profile_photo(user_id)),
    }


@app.get("/api/v1/users/{user_id}/jobs", dependencies=[Depends(require_owner_or_admin)])
def list_user_jobs(user_id: int = PathParam(..., ge=1)):
    """Jobs List 同源数据（筛选在客户端做）。"""
    from db.session import get_db
    from services.jobs_list_data import (
        fetch_jobs_list_rows,
        job_ids_with_cover_letter_assets,
        map_job_tailored_resume_flags,
    )

    from services.saved_jobs_service import SavedJobsService

    # 打分模型对比展示（主分数 + deepseek 对比分数逐项并排）：由 settings.jobs_list_model_comparison
    # 控制，默认关闭——见 services/jobs_list_data.py::fetch_jobs_list_rows 的
    # include_model_comparison 参数
    model_comparison_enabled = settings.jobs_list_model_comparison
    with get_db() as db:
        jobs = fetch_jobs_list_rows(
            db, user_id, include_model_comparison=model_comparison_enabled
        )
        jids = [j["id"] for j in jobs]
        has_letter = job_ids_with_cover_letter_assets(db, user_id, jids)
        tailored = map_job_tailored_resume_flags(db, user_id, jids)
        saved_ids = SavedJobsService(db).list_saved_job_ids(user_id)
    for j in jobs:
        j["has_cover_letter_asset"] = j["id"] in has_letter
        j["has_resume_asset"] = bool(tailored.get(j["id"]))
        j["has_tailored_resume"] = bool(tailored.get(j["id"]))
        j["is_saved"] = j["id"] in saved_ids
    return {
        "user_id": user_id,
        "jobs_list_debug_show_all": settings.jobs_list_debug_show_all,
        "jobs_list_diagnostics": settings.jobs_list_diagnostics,
        "jobs_list_model_comparison": model_comparison_enabled,
        "compare_model_name": settings.deepseek_model_name if model_comparison_enabled else None,
        "score_threshold_review": settings.score_threshold_review,
        "score_threshold_generate": settings.score_threshold_generate,
        "jobs": jobs,
    }


def _save_job_sync(user_id: int, job_id: int) -> None:
    from db.session import get_db
    from services.saved_jobs_service import SavedJobsService

    with get_db() as db:
        SavedJobsService(db).save_job(user_id, job_id)


def _unsave_job_sync(user_id: int, job_id: int) -> None:
    from db.session import get_db
    from services.saved_jobs_service import SavedJobsService

    with get_db() as db:
        SavedJobsService(db).unsave_job(user_id, job_id)


@app.post("/api/v1/users/{user_id}/jobs/{job_id}/save", dependencies=[Depends(require_owner_or_admin)])
async def save_job(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
):
    """收藏关注（跟投递跟进完全独立，轻量 DB 写，不占生成信号量）。"""
    try:
        await run_in_threadpool(_save_job_sync, user_id, job_id)
    except Exception as e:
        logger.exception("save_job user=%s job=%s: %s", user_id, job_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "job_id": job_id}


@app.delete("/api/v1/users/{user_id}/jobs/{job_id}/save", dependencies=[Depends(require_owner_or_admin)])
async def unsave_job(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
):
    try:
        await run_in_threadpool(_unsave_job_sync, user_id, job_id)
    except Exception as e:
        logger.exception("unsave_job user=%s job=%s: %s", user_id, job_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "job_id": job_id}


@app.get("/api/v1/users/{user_id}/saved-jobs", dependencies=[Depends(require_owner_or_admin)])
def list_saved_jobs(user_id: int = PathParam(..., ge=1)):
    from db.session import get_db
    from services.saved_jobs_service import SavedJobsService

    with get_db() as db:
        rows = SavedJobsService(db).list_saved_jobs(user_id)
    return {"user_id": user_id, "jobs": rows}


class MarkAppliedBody(BaseModel):
    resume_choice: Literal["slot_1", "slot_2", "tailored"]


@app.post("/api/v1/users/{user_id}/jobs/{job_id}/mark-applied", dependencies=[Depends(require_owner_or_admin)])
async def mark_applied(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
    body: MarkAppliedBody = Body(...),
):
    """标记岗位为已投递（轻量 DB 写，不占生成信号量）；resume_choice 指定投递快照用哪份简历。"""
    try:
        await run_in_threadpool(_mark_applied_sync, user_id, job_id, body.resume_choice)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("mark_applied user=%s job=%s: %s", user_id, job_id, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"ok": True, "user_id": user_id, "job_id": job_id}


@app.post("/api/v1/users/{user_id}/jobs/{job_id}/reorganize-resume", dependencies=[Depends(require_owner_or_admin)])
async def reorganize_resume_placeholder(
    user_id: int = PathParam(..., ge=1),
    job_id: int = PathParam(..., ge=1),
):
    """
    重组简历（占位）：后续将接入 LLM/流水线。当前仅返回成功占位，供前端按钮联调。
    """
    return {
        "ok": True,
        "user_id": user_id,
        "job_id": job_id,
        "message": "重组简历流程尚未实现，接口已预留。",
    }


if HAS_FRONTEND_SPA:
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.staticfiles import StaticFiles

    class SPAStaticFiles(StaticFiles):
        """Client-side routes (/app/jobs, /app/settings, ...) only exist as React
        Router paths — there's no matching file on disk for them, so a direct load or
        browser refresh on one hits this mount as a plain GET and 404s by default.
        Fall back to index.html so React Router can resolve the route client-side,
        same as any standard SPA deployment behind a static file server."""

        async def get_response(self, path, scope):
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code == 404:
                    return await super().get_response("index.html", scope)
                raise

    app.mount(
        "/app",
        SPAStaticFiles(directory=str(_FRONTEND_DIST), html=True),
        name="spa",
    )
