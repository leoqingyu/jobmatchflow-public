from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class UserSavedJob(Base):
    """
    用户主动"收藏关注"的岗位——跟 ApplicationTracking（投递后跟进）完全独立：收藏不代表
    投递，也不会自动转成 ApplicationTracking 记录；用户之后仍需手动点 mark-applied。
    """

    __tablename__ = "user_saved_jobs"
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_user_saved_jobs_user_job"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    def __repr__(self) -> str:
        return f"<UserSavedJob user={self.user_id} job={self.job_id}>"
