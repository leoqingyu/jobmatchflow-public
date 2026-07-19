from datetime import datetime

from sqlalchemy import Integer, String, Boolean, JSON, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    target_regions: Mapped[list] = mapped_column(JSON, default=list)
    target_domains: Mapped[list] = mapped_column(JSON, default=list)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    preferred_seniority: Mapped[list] = mapped_column(JSON, default=list)
    scoring_threshold_review: Mapped[int] = mapped_column(Integer, default=70)
    scoring_threshold_generate: Mapped[int] = mapped_column(Integer, default=90)
    notification_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 由多份上传简历经 LLM 汇总成的 HTML 素材库，供评分缓存与 JD 对照
    cv_material_library_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    cv_material_library_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    # 可编辑的 Master CV 结构化 JSON（按 cv_templet_without_photo.html 字段）
    master_cv_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 用户补充的求职/岗位偏好（短文本），并入评分缓存上下文
    scoring_preferences_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 实习/全职偏好（core.constants.EmploymentTypePreference），打分流水线最前置的硬性过滤——
    # 见 services/scoring_service.py 里 Step 1 向量初筛之后、Step 3 逐项匹配之前的 gate。
    employment_type_preference: Mapped[str] = mapped_column(String(20), default="both")
    # 简历/求职信生成用哪个 LLM（core.constants.GenerationModel："gemini" | "claude"），
    # 在 Settings 页选，见 services/resume_generation_service.py / cover_letter_generation_service.py
    # 怎么按这个字段挑 provider。
    generation_model: Mapped[str] = mapped_column(String(20), default="gemini")
    # 简历 bullet 改写尺度（core.constants.ResumeTailoringMode："honest" | "jd_aligned"），
    # 在 Settings 页选，见 ai/resume_rewrite.py 怎么按这个字段挑 bullet 改写 prompt。
    resume_tailoring_mode: Mapped[str] = mapped_column(String(20), default="honest")
    # 配额（admin 后台设置，见 api/web_routes.py::api_admin_put_user_quota）：NULL = 不限。
    # 打分额度用于 gate services/user_cv_lookup.py::list_user_ids_ready_for_scoring；
    # 生成额度用于 gate services/quota_service.py::check_generation_quota。
    max_matched_jobs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_generated_resumes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_generated_cover_letters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # True 一旦 admin 通过 PUT /admin/users/{id}/quota 手动改过这个用户的配额（见
    # api_admin_put_user_quota）。纯展示/追踪用途：区分"还是注册时给的免费默认额度"
    # 还是"admin 已经手动调过（比如付费解锁）"，不参与任何配额判断逻辑本身。
    quota_overridden_by_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # 用户可选求职国家的上限集合（admin 控制，区别于用户自己在 Settings 选的
    # UserSearchProfile.countries——那是"选了哪些"，这个是"最多能选哪些"，见
    # services/profile_service.py::upsert_default_search_profile_countries 的二次交集）。
    # Python-level default must match the migration's server_default (t1u2v3w4x5y6) —
    # `default=list` (empty list) would silently mean "no restriction at all" for every
    # ORM-created row (SQLAlchemy uses the Python-side default over the DB's
    # server_default whenever the column is omitted from an INSERT), defeating the
    # ceiling for every real signup. Caught via a real onboarding-flow test (2026-07-19):
    # a fresh signup got allowed_countries=[] instead of the intended 2-country default.
    allowed_countries: Mapped[list] = mapped_column(JSON, default=lambda: ["Switzerland", "Luxembourg"])
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="profile")  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserProfile user_id={self.user_id}>"
