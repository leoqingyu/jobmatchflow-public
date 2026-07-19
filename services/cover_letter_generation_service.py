"""
求职信生成：直接复用已生成好的定制简历内容（选材+改写后的 bullet）+ 岗位公司信息，一次 LLM
调用产出信件正文，DOCX 渲染。跟简历生成不是同一套重活——没有选材算法、没有字数预算重试、
没有防编造校验（信任简历那一层已经把关过），是有意做成的"简单小操作"。

必须先有定制简历（ResumeGenerationService.generate_for_job）才能生成求职信——求职信的内容
就是简历已经选好、改写好、fact-check 过的 bullet，不再重新判断"哪段经历跟这个 JD 相关"。
"""

from __future__ import annotations

import base64

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.constants import AssetStatus, AssetType
from core.llm_usage_tracking import llm_usage_context
from core.logger import get_logger
from ai.cover_letter_generator import generate_cover_letter
from ai.llm_factory import get_generation_llm_client
from db.models import GeneratedAsset, Job
from renderer.docx_render import render_cover_letter_docx, render_preview_png
from services.cover_letter_storage import write_cover_letter_docx
from services.profile_service import ProfileService
from services.resume_generation_service import ResumeGenerationService
from services.resume_storage import remove_file_if_exists

logger = get_logger(__name__)


def render_letter_preview(db: Session, asset_id: int, user_id: int, draft_payload: dict) -> dict:
    """Materials Editor 求职信实时预览：跟 services.resume_generation_service.render_resume_preview
    同一个模式（草稿合并、不落库、渲染 DOCX 再转低分辨率缩略图），只是渲染求职信而不是简历。"""
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    if asset.asset_type != AssetType.MOTIVATION_LETTER.value:
        raise ValueError("仅支持 motivation_letter 类型")

    merged = {**(asset.content_json or {}), **draft_payload}
    docx_bytes = render_cover_letter_docx(merged)
    pages, png_bytes = render_preview_png(docx_bytes)
    return {
        "pages": pages,
        "thumbnail_png_base64": base64.b64encode(png_bytes).decode("ascii") if png_bytes else None,
    }


class CoverLetterGenerationService:
    def __init__(self, db: Session, user_id: int | None = None):
        """跟 ResumeGenerationService 同一套按 UserProfile.generation_model 选 Gemini/Claude
        的逻辑，见那边 __init__ 的说明。"""
        self.db = db
        model_choice = ProfileService(db).get_generation_model(user_id) if user_id is not None else None
        self.llm = get_generation_llm_client(model_choice)

    def generate_for_job(self, user_id: int, job_id: int) -> GeneratedAsset:
        with llm_usage_context(user_id):
            return self._generate_for_job_impl(user_id, job_id)

    def _generate_for_job_impl(self, user_id: int, job_id: int) -> GeneratedAsset:
        job = self.db.get(Job, job_id)
        if not job:
            raise ValueError("Job not found")

        resume_asset = ResumeGenerationService(self.db, user_id).get_resume_asset(user_id, job_id)
        if not resume_asset or not resume_asset.content_json:
            raise ValueError("This job doesn't have a tailored resume yet — generate the resume first")

        resume_content = resume_asset.content_json
        resume_bullets = [
            b
            for entry in resume_content.get("experience") or []
            for b in entry.get("bullets") or []
        ]
        must_have_requirements = [
            r
            for r in (job.structured_requirements or {}).get("requirements") or []
            if r.get("importance") == "must"
        ]

        letter = generate_cover_letter(
            self.llm,
            candidate_name=resume_content.get("full_name") or "",
            company=job.company or "",
            job_title=job.title or "",
            must_have_requirements=must_have_requirements,
            resume_bullets=resume_bullets,
            job_description=job.description_clean or job.description_raw or "",
        )

        content = {
            "full_name": resume_content.get("full_name") or "",
            "location": resume_content.get("location") or "",
            "phone": resume_content.get("phone") or "",
            "email": resume_content.get("email") or "",
            "company": job.company or "",
            "job_title": job.title or "",
            # 开头称呼/结尾落款措辞是固定套话，不值得为这个单独调 LLM——存成结构化字段只是
            # 为了让 Materials Editor 能编辑，默认值原样保留渲染层原来的硬编码文案。
            "greeting": "Dear Hiring Team,",
            "closing": "Sincerely,",
            "paragraphs": letter.get("paragraphs") or [],
            "llm_model": self.llm.model_name,
        }

        asset = self._save_letter_asset(None, user_id, job_id, content)
        docx_bytes = render_cover_letter_docx(content)
        path = write_cover_letter_docx(docx_bytes, user_id, job_id, asset.file_path)
        asset.file_path = path
        self.db.flush()
        logger.info("求职信生成完成 user_id=%s job_id=%s", user_id, job_id)
        return asset

    def get_letter_asset(self, user_id: int, job_id: int) -> GeneratedAsset | None:
        """公开只读查询，供下载等接口直接用。"""
        return self._get_letter_asset(user_id, job_id)

    def _get_letter_asset(self, user_id: int, job_id: int) -> GeneratedAsset | None:
        return (
            self.db.query(GeneratedAsset)
            .filter(
                GeneratedAsset.user_id == user_id,
                GeneratedAsset.job_id == job_id,
                GeneratedAsset.asset_type == AssetType.MOTIVATION_LETTER.value,
            )
            .order_by(GeneratedAsset.id.desc())
            .first()
        )

    def _save_letter_asset(
        self, existing: GeneratedAsset | None, user_id: int, job_id: int, content: dict
    ) -> GeneratedAsset:
        if existing:
            remove_file_if_exists(existing.file_path)
            existing.content_json = content
            existing.content_text = None
            existing.llm_model = self.llm.model_name
            existing.status = AssetStatus.DONE.value
            existing.file_path = None
            self.db.flush()
            return existing
        asset = GeneratedAsset(
            user_id=user_id,
            job_id=job_id,
            asset_type=AssetType.MOTIVATION_LETTER.value,
            content_json=content,
            content_text=None,
            storage_provider="local",
            llm_model=self.llm.model_name,
            status=AssetStatus.DONE.value,
            file_path=None,
        )
        try:
            self.db.add(asset)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            again = self._get_letter_asset(user_id, job_id)
            if again:
                return self._save_letter_asset(again, user_id, job_id, content)
            raise
        return asset


def update_cover_letter_content(db: Session, asset_id: int, user_id: int, payload: dict) -> str:
    """用户手动编辑求职信 content_json 后，直接按编辑结果重新渲染 DOCX 并覆盖旧文件。"""
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    if asset.asset_type != AssetType.MOTIVATION_LETTER.value:
        raise ValueError("仅支持 motivation_letter 类型")

    merged = {**(asset.content_json or {}), **payload}
    docx_bytes = render_cover_letter_docx(merged)
    path = write_cover_letter_docx(docx_bytes, user_id, asset.job_id, asset.file_path)
    asset.content_json = merged
    asset.file_path = path
    db.flush()
    return path
