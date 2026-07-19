"""
定制简历生成接口：选材+改写+DOCX 渲染的编排入口，风格与 api/experience_routes.py /
api/web_routes.py 一致——裸路由函数 + 内联 Pydantic model + `with get_db() as db:`。
每条路由都是 /users/{user_id}/...，鉴权由 router 级 dependencies 统一处理，见
api/auth_deps.py::require_owner_or_admin。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import Response

from api.auth_deps import require_owner_or_admin
from core.logger import get_logger
from db.models import Job
from db.session import get_db
from services.cover_letter_generation_service import CoverLetterGenerationService
from services.resume_generation_service import ResumeGenerationService

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1", tags=["resume"], dependencies=[Depends(require_owner_or_admin)]
)

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _resume_asset_payload(asset) -> dict:
    content = asset.content_json or {}
    return {
        "id": asset.id,
        "job_id": asset.job_id,
        "content": content,
        "has_file": bool(asset.file_path),
        "llm_model": asset.llm_model,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


def _letter_asset_payload(asset) -> dict:
    content = asset.content_json or {}
    return {
        "id": asset.id,
        "job_id": asset.job_id,
        "content": content,
        "has_file": bool(asset.file_path),
        "llm_model": asset.llm_model,
        "updated_at": asset.updated_at.isoformat() if asset.updated_at else None,
    }


@router.post("/users/{user_id}/jobs/{job_id}/resume/generate")
def api_generate_resume(user_id: int, job_id: int):
    with get_db() as db:
        try:
            asset = ResumeGenerationService(db, user_id).generate_for_job(user_id, job_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _resume_asset_payload(asset)


class ResumeSelectionBody(BaseModel):
    unit_ids_in_order: list[int] = Field(..., min_length=1)


@router.put("/users/{user_id}/jobs/{job_id}/resume/selection")
def api_adjust_resume_selection(user_id: int, job_id: int, body: ResumeSelectionBody):
    with get_db() as db:
        try:
            asset = ResumeGenerationService(db, user_id).apply_selection_adjustment(
                user_id, job_id, body.unit_ids_in_order
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _resume_asset_payload(asset)


@router.get("/users/{user_id}/jobs/{job_id}/resume/download")
def api_download_resume(user_id: int, job_id: int):
    with get_db() as db:
        asset = ResumeGenerationService(db).get_resume_asset(user_id, job_id)
        if not asset or not asset.file_path:
            raise HTTPException(status_code=404, detail="简历尚未生成")
        path = Path(asset.file_path)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="简历文件缺失，请重新生成")
        data = path.read_bytes()
        full_name = (asset.content_json or {}).get("full_name") or "resume"
        filename = f"{full_name.replace(' ', '_')}_resume.docx"
        return Response(
            content=data,
            media_type=_DOCX_MIME,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )


@router.get("/users/{user_id}/jobs/{job_id}/materials")
def api_job_materials(user_id: int, job_id: int):
    """简历编辑器（新窗口）的单一入口：一次拿到该岗位标题/公司 + 已生成的简历/求职信资产，
    避免为了编辑一个岗位而拉取用户全部资产历史。"""
    with get_db() as db:
        job = db.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="岗位不存在")
        resume_asset = ResumeGenerationService(db).get_resume_asset(user_id, job_id)
        letter_asset = CoverLetterGenerationService(db).get_letter_asset(user_id, job_id)
        return {
            "job": {"id": job.id, "title": job.title, "company": job.company},
            "resume": _resume_asset_payload(resume_asset) if resume_asset else None,
            "cover_letter": _letter_asset_payload(letter_asset) if letter_asset else None,
        }


@router.post("/users/{user_id}/jobs/{job_id}/cover-letter/generate")
def api_generate_cover_letter(user_id: int, job_id: int):
    with get_db() as db:
        try:
            asset = CoverLetterGenerationService(db, user_id).generate_for_job(user_id, job_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        return _letter_asset_payload(asset)
