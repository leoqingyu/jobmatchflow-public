from datetime import datetime

from sqlalchemy import Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class LlmUsageLog(Base):
    """Raw token counts per LLM call, for cost estimation (see core/llm_pricing.py
    for $/token rates, computed at query time — not stored here, so a pricing
    change never requires rewriting historical rows).

    user_id is nullable: some calls (e.g. job-level JD extraction, which is
    cached per Job and shared across users) aren't attributable to one user."""

    __tablename__ = "llm_usage_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    task_name: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="llm_usage_logs")  # noqa: F821

    def __repr__(self) -> str:
        return f"<LlmUsageLog id={self.id} user={self.user_id} model={self.model_name} tokens={self.total_tokens}>"
