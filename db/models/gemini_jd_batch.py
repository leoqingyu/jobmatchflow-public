from datetime import datetime

from sqlalchemy import Integer, String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class GeminiJdBatch(Base):
    """一次 Gemini Batch API 提交（JD 结构化提取，job 级，见 services/gemini_jd_batch_service.py）。
    status: submitted | succeeded | failed | expired | cancelled。"""

    __tablename__ = "gemini_jd_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="submitted")
    job_count: Mapped[int] = mapped_column(Integer, default=0)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    jobs: Mapped[list["Job"]] = relationship(back_populates="jd_extraction_batch")  # noqa: F821

    def __repr__(self) -> str:
        return f"<GeminiJdBatch id={self.id} status={self.status} jobs={self.job_count}>"
