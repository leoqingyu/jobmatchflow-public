from datetime import datetime

from sqlalchemy import String, Text, JSON, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class GeneratedAsset(Base):
    __tablename__ = "generated_assets"
    __table_args__ = (
        # 资产是版本历史；投递记录引用某一版本，后续重新生成不能覆盖历史材料。
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(100), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    storage_provider: Mapped[str] = mapped_column(String(50), default="local")
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="generated_assets")  # noqa: F821
    job: Mapped["Job"] = relationship(back_populates="generated_assets")  # noqa: F821

    def __repr__(self) -> str:
        return f"<GeneratedAsset user={self.user_id} job={self.job_id} type={self.asset_type}>"
