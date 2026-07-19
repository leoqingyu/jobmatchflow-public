from datetime import datetime

from sqlalchemy import String, Boolean, JSON, Text, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserMasterCV(Base):
    __tablename__ = "user_master_cvs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    cv_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 母简历正文（.md / .txt 上传），供评分、初筛、定制简历生成时直接给 LLM 阅读
    cv_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 历史/可选字段，新流程可不使用
    cv_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cv_master_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="master_cvs")  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserMasterCV id={self.id} user_id={self.user_id} name={self.cv_name}>"
