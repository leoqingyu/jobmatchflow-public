from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserExperiencePreferenceEvent(Base):
    """
    简历选材偏好日志：只增不改，纯事件流。记录用户在生成简历时对某条经历单元的
    取舍/调序动作，聚合逻辑（ai/resume_selection.py 里的 preference_adjustment）在读的时候算。

    job_context 是事件发生时该 job 的 domain 快照（不是外键动态取）——偏好是有语境的
    （同一条经历，投"量化开发"想突出，投"合规分析"不想提），语境按 Job.domain 这类粗桶
    聚合才互相通用；快照是为了防止以后 Job.domain 被重新分类后污染历史信号。
    """

    __tablename__ = "user_experience_preference_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("user_experience_units.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    # "selected_by_ai"|"removed_by_user"|"added_by_user"|"reordered"，见 core.constants.PreferenceAction
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    job_context: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="experience_preference_events")  # noqa: F821
    item: Mapped["UserExperienceUnit"] = relationship()  # noqa: F821
    job: Mapped["Job"] = relationship()  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<UserExperiencePreferenceEvent user={self.user_id} item={self.item_id} "
            f"job={self.job_id} action={self.action}>"
        )
