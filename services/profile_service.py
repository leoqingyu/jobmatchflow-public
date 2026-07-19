from pathlib import Path
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session

from core.constants import (
    MAX_USER_SAVED_RESUMES,
    SCORING_PREFERENCES_MAX_CHARS,
    EmploymentTypePreference,
    GenerationModel,
    ResumeTailoringMode,
)
from core.config import settings
from core.logger import get_logger
from core.job_markets import sanitize_user_countries
from db.models import UserProfile, UserSearchProfile, UserMasterCV, UserJobScore

logger = get_logger(__name__)


class ProfileService:
    def __init__(self, db: Session):
        self.db = db

    def get_profile(self, user_id: int) -> UserProfile | None:
        return self.db.query(UserProfile).filter(UserProfile.user_id == user_id).first()

    def ensure_profile_row(self, user_id: int) -> UserProfile:
        p = self.get_profile(user_id)
        if not p:
            p = UserProfile(user_id=user_id)
            self.db.add(p)
            self.db.flush()
        return p

    def upsert_profile(self, user_id: int, **kwargs) -> UserProfile:
        profile = self.get_profile(user_id)
        if not profile:
            profile = UserProfile(user_id=user_id, **kwargs)
            self.db.add(profile)
        else:
            for k, v in kwargs.items():
                setattr(profile, k, v)
        self.db.flush()
        return profile

    def set_scoring_preferences_text(self, user_id: int, text: str | None) -> UserProfile:
        """用户补充的求职偏好，并入 Pipeline 评分上下文（有长度上限）。"""
        raw = (text or "").strip()
        if len(raw) > SCORING_PREFERENCES_MAX_CHARS:
            raise ValueError(
                f"偏好文本最多 {SCORING_PREFERENCES_MAX_CHARS} 字（当前 {len(raw)} 字）"
            )
        p = self.ensure_profile_row(user_id)
        p.scoring_preferences_text = raw or None
        self.db.flush()
        return p

    def set_employment_type_preference(self, user_id: int, preference: str) -> UserProfile:
        """实习/全职求职偏好——打分流水线最前置的硬性过滤，见 ai/scoring_rules.py::employment_type_mismatch。"""
        valid = {e.value for e in EmploymentTypePreference}
        if preference not in valid:
            raise ValueError(f"employment_type_preference 必须是 {sorted(valid)} 之一")
        p = self.ensure_profile_row(user_id)
        p.employment_type_preference = preference
        self.db.flush()
        return p

    def get_generation_model(self, user_id: int) -> str | None:
        """简历/求职信生成该用的 LLM，None 表示还没存过这个字段（老用户/新建 profile）——
        调用方（ai/llm_factory.py::get_generation_llm_client）把 None 当默认值 "gemini" 处理。"""
        p = self.get_profile(user_id)
        return p.generation_model if p else None

    def set_generation_model(self, user_id: int, model: str) -> UserProfile:
        """简历/求职信生成用哪个 LLM——见 ai/llm_factory.py::get_generation_llm_client
        怎么按这个字段在 Gemini/Claude 之间切换。"""
        valid = {m.value for m in GenerationModel}
        if model not in valid:
            raise ValueError(f"generation_model 必须是 {sorted(valid)} 之一")
        p = self.ensure_profile_row(user_id)
        p.generation_model = model
        self.db.flush()
        return p

    def get_resume_tailoring_mode(self, user_id: int) -> str | None:
        """简历 bullet 改写尺度，None 表示还没存过这个字段——调用方
        （ai/resume_rewrite.py）把 None 当默认值 "honest" 处理。"""
        p = self.get_profile(user_id)
        return p.resume_tailoring_mode if p else None

    def set_resume_tailoring_mode(self, user_id: int, mode: str) -> UserProfile:
        """简历 bullet 改写用"honest"（只重组原文事实）还是"jd_aligned"（更贴 JD、允许
        合理专业推断）——见 core.constants.ResumeTailoringMode 的边界说明。"""
        valid = {m.value for m in ResumeTailoringMode}
        if mode not in valid:
            raise ValueError(f"resume_tailoring_mode 必须是 {sorted(valid)} 之一")
        p = self.ensure_profile_row(user_id)
        p.resume_tailoring_mode = mode
        self.db.flush()
        return p

    def get_active_search_profiles(self, user_id: int) -> list[UserSearchProfile]:
        return (
            self.db.query(UserSearchProfile)
            .filter(UserSearchProfile.user_id == user_id, UserSearchProfile.is_active == True)  # noqa: E712
            .all()
        )

    @staticmethod
    def get_only_master_cv_query(db: Session, user_id: int):
        """按 id 升序；首条常用于兼容旧「单母简历」接口。"""
        return (
            db.query(UserMasterCV)
            .filter(UserMasterCV.user_id == user_id)
            .order_by(UserMasterCV.id.asc())
        )

    @staticmethod
    def list_master_cvs(db: Session, user_id: int) -> list[UserMasterCV]:
        return (
            db.query(UserMasterCV)
            .filter(UserMasterCV.user_id == user_id)
            .order_by(UserMasterCV.id.asc())
            .all()
        )

    @staticmethod
    def save_cv_upload_file(user_id: int, original_filename: str, data: bytes) -> str:
        base = Path(settings.local_storage_base_path) / "user_cv_uploads" / str(user_id)
        base.mkdir(parents=True, exist_ok=True)
        raw = (original_filename or "cv").replace("\x00", "")[-120:]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw) or "cv"
        name = f"{uuid4().hex[:12]}_{safe}"
        path = base / name
        path.write_bytes(data)
        return str(path.resolve())

    @staticmethod
    def save_cv_pdf_file(user_id: int, original_filename: str, data: bytes) -> str:
        """保存简历库 PDF，扩展名固定为 .pdf。"""
        from uuid import uuid4

        base = Path(settings.local_storage_base_path) / "user_cv_uploads" / str(user_id)
        base.mkdir(parents=True, exist_ok=True)
        raw = (original_filename or "cv").replace("\x00", "")[-100:]
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in raw) or "cv"
        stem = Path(safe).stem or "cv"
        name = f"{uuid4().hex[:12]}_{stem}.pdf"
        path = base / name
        path.write_bytes(data)
        return str(path.resolve())

    def add_resume_pdf_slot(
        self,
        user_id: int,
        cv_name: str,
        pdf_bytes: bytes,
        original_filename: str,
    ) -> UserMasterCV:
        """新增一条简历库记录：仅存 PDF，并抽取文本写入 cv_markdown。"""
        from core.pdf_text import extract_text_from_pdf_bytes

        if not pdf_bytes or not pdf_bytes.strip().startswith(b"%PDF"):
            raise ValueError("请上传有效的 PDF 文件")
        text = extract_text_from_pdf_bytes(pdf_bytes)
        if len(text.strip()) < 12:
            raise ValueError(
                "PDF 中未能提取到足够文本；若为扫描件请使用含文字层的 PDF（暂不支持 OCR）"
            )
        path_str = self.save_cv_pdf_file(user_id, original_filename, pdf_bytes)
        return self.add_master_cv_slot(
            user_id,
            cv_name,
            text,
            source_file_path=path_str,
        )

    def upsert_resume_pdf_slot(
        self,
        user_id: int,
        slot_number: int,
        cv_name: str,
        pdf_bytes: bytes,
        original_filename: str,
    ) -> UserMasterCV:
        """
        固定 2 槽位简历（Settings 页"简历1/简历2"）：槽位按 id 升序位置识别
        （第 1 条=槽位1，第 2 条=槽位2），槽位已有记录时原地更新（同 id，不新增行、
        不影响另一槽位的排序位置），槽位为空时新增。与 add_resume_pdf_slot 的
        "总是新增一条（最多 MAX_USER_SAVED_RESUMES 条）"不同，这里固定上限为 2。
        """
        from core.pdf_text import extract_text_from_pdf_bytes

        if slot_number not in (1, 2):
            raise ValueError("slot_number 必须是 1 或 2")
        if not pdf_bytes or not pdf_bytes.strip().startswith(b"%PDF"):
            raise ValueError("请上传有效的 PDF 文件")
        text = extract_text_from_pdf_bytes(pdf_bytes)
        if len(text.strip()) < 12:
            raise ValueError(
                "PDF 中未能提取到足够文本；若为扫描件请使用含文字层的 PDF（暂不支持 OCR）"
            )

        rows = self.list_master_cvs(self.db, user_id)
        idx = slot_number - 1
        name = (cv_name or f"Resume {slot_number}").strip() or f"Resume {slot_number}"
        path_str = self.save_cv_pdf_file(user_id, original_filename, pdf_bytes)

        if idx < len(rows):
            row = rows[idx]
            old_fp = (row.source_file_path or "").strip()
            row.cv_name = name
            row.cv_markdown = text
            row.cv_json = None
            row.cv_master_html = None
            row.source_file_path = path_str
            row.is_default = True
            self.db.flush()
            if old_fp and old_fp != path_str:
                try:
                    Path(old_fp).unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("删除旧简历文件失败 path=%s: %s", old_fp, e)
            logger.info("已更新简历槽位 user_id=%s slot=%s cv_id=%s", user_id, slot_number, row.id)
            return row

        if len(rows) >= 2:
            raise ValueError("最多保存 2 份简历")
        return self.add_master_cv_slot(user_id, name, text, source_file_path=path_str)

    def add_master_cv_slot(
        self,
        user_id: int,
        cv_name: str,
        text: str,
        *,
        source_file_path: str | None = None,
    ) -> UserMasterCV:
        """新增一条简历（最多 MAX_USER_SAVED_RESUMES 条），不删除其它条目。"""
        body = (text or "").strip()
        if not body:
            raise ValueError("简历正文为空")

        n = (
            self.db.query(UserMasterCV)
            .filter(UserMasterCV.user_id == user_id)
            .count()
        )
        if n >= MAX_USER_SAVED_RESUMES:
            raise ValueError(f"已达上传上限（{MAX_USER_SAVED_RESUMES} 份），请先删除再添加")

        cv = UserMasterCV(
            user_id=user_id,
            cv_name=(cv_name or "Resume").strip() or "Resume",
            cv_markdown=body,
            cv_json=None,
            cv_master_html=None,
            source_file_path=(source_file_path or "").strip() or None,
            is_default=True,
        )
        self.db.add(cv)
        self.db.flush()
        logger.info("新增用户简历 user_id=%s cv_id=%s", user_id, cv.id)
        return cv

    def upsert_default_master_file(
        self,
        user_id: int,
        cv_name: str,
        text: str,
    ) -> UserMasterCV:
        """
        兼容旧行为：更新该用户 id 最小的那条简历；若无则创建。
        不再删除同一用户的其它简历行。
        """
        body = (text or "").strip()
        if not body:
            raise ValueError("母简历内容为空")

        rows = self.get_only_master_cv_query(self.db, user_id).all()
        if not rows:
            return self.add_master_cv_slot(
                user_id, cv_name, body, source_file_path=None
            )

        kept = rows[0]
        kept.cv_name = (cv_name or "Resume").strip() or "Resume"
        kept.cv_markdown = body
        kept.cv_json = None
        kept.cv_master_html = None
        kept.is_default = True
        self.db.flush()
        logger.info("已更新首条简历（文本） user_id=%s id=%s", user_id, kept.id)
        return kept

    def delete_master_cv(self, user_id: int, cv_id: int) -> None:
        row = self.db.get(UserMasterCV, cv_id)
        if not row or row.user_id != user_id:
            raise ValueError("简历不存在或无权删除")

        self.db.execute(
            update(UserJobScore)
            .where(UserJobScore.master_cv_id == cv_id)
            .values(master_cv_id=None)
        )
        self.db.execute(
            update(UserJobScore)
            .where(UserJobScore.recommended_cv_id == cv_id)
            .values(recommended_cv_id=None)
        )

        fp = (row.source_file_path or "").strip()
        if fp:
            try:
                Path(fp).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("删除简历文件失败 path=%s: %s", fp, e)

        self.db.delete(row)
        self.db.flush()
        logger.info("已删除简历 user_id=%s cv_id=%s", user_id, cv_id)

    def get_default_search_profile(self, user_id: int) -> UserSearchProfile | None:
        return (
            self.db.query(UserSearchProfile)
            .filter(UserSearchProfile.user_id == user_id, UserSearchProfile.is_active == True)  # noqa: E712
            .order_by(UserSearchProfile.id.asc())
            .first()
        )

    def upsert_default_search_profile_countries(
        self, user_id: int, countries: list[str]
    ) -> UserSearchProfile:
        clean = sanitize_user_countries(countries)
        # 二次交集：admin 在后台设置的 allowed_countries 是"最多能选哪些"的上限
        # （UserProfile.allowed_countries，见 db/models/user_profile.py 注释），用户在
        # Settings 页自己选的这份 countries 不能超出这个上限。留空视为未设限制（不过滤）。
        profile = self.get_profile(user_id)
        allowed = list(profile.allowed_countries) if profile and profile.allowed_countries else []
        if allowed:
            clean = [c for c in clean if c in allowed]
        sp = self.get_default_search_profile(user_id)
        if not sp:
            sp = UserSearchProfile(
                user_id=user_id,
                profile_name="default",
                keywords=[],
                countries=clean,
                sources=["jobspy"],
                frequency_hours=6,
                is_active=True,
            )
            self.db.add(sp)
        else:
            sp.countries = clean
        self.db.flush()
        logger.info("已更新求职国家 user_id=%s countries=%s", user_id, clean)
        return sp

    def choose_locked_country(self, user_id: int, country: str) -> UserSearchProfile:
        """Onboarding: the user's one-time, exclusive country pick. Narrows
        UserProfile.allowed_countries down to exactly this one country — that's what
        actually locks it, since every future write to UserSearchProfile.countries
        (this call or the regular Settings save) gets clamped to this same narrowed
        ceiling by upsert_default_search_profile_countries above. Once locked, changing
        it requires an admin edit via PUT /admin/users/{id}/quota — there's no
        self-service path back, by design.
        """
        profile = self.get_profile(user_id)
        current_ceiling = list(profile.allowed_countries) if profile and profile.allowed_countries else []
        if len(current_ceiling) <= 1:
            raise ValueError("Country already set — contact the admin to change it.")
        if country not in current_ceiling:
            raise ValueError(f"'{country}' is not one of the available options: {current_ceiling}")
        profile.allowed_countries = [country]
        self.db.flush()
        return self.upsert_default_search_profile_countries(user_id, [country])
        return sp
