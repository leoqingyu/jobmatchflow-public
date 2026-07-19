from datetime import datetime

from sqlalchemy import String, Text, JSON, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class ApplicationTracking(Base):
    __tablename__ = "application_tracking"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_application_tracking_user_job"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    application_status: Mapped[str] = mapped_column(String(50), default="new")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_stage_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 标记「已投递」时快照：定制简历资产 > 简历库推荐；求职信为当时存在的生成资产
    applied_resume_master_cv_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_master_cvs.id"), nullable=True
    )
    applied_resume_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_assets.id"), nullable=True
    )
    applied_cover_letter_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("generated_assets.id"), nullable=True
    )
    # 投递时的不可变内容快照；不能依赖 GeneratedAsset/UserMasterCV 的当前内容。
    applied_resume_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    applied_cover_letter_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    applied_resume_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    applied_cover_letter_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    jd_snapshot_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    score_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status_history: Mapped[list | None] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="application_trackings")  # noqa: F821
    job: Mapped["Job"] = relationship(back_populates="application_trackings")  # noqa: F821

    def __repr__(self) -> str:
        return f"<ApplicationTracking user={self.user_id} job={self.job_id} status={self.application_status}>"
