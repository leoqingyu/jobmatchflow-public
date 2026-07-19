from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserCandidateFacts(Base):
    """
    确定性事实：每用户一行，取代旧流程的 UserProfile.structured_profile。

    atoms 是唯一数据源（不额外拆 skills/languages/... 列），每条形如
    {"id": "skill_python", "type": "skill|language|education|certification|
    work_authorization|industry", "label": "...", "detail": {...}}。id 是稳定字符串，
    供打分 Step 3 的 evidence_ids 引用。技能类 atom 的 label 写入前应过
    core.skill_aliases.normalize_skill_label 做别名归一化。

    total_years_experience 单独存一列，因为它是一个数、不是可枚举的 atom。
    """

    __tablename__ = "user_candidate_facts"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    atoms: Mapped[list] = mapped_column(JSON, default=list)
    total_years_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 数据来源，如 "extracted_from_master_cv" / "manual"
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 为将来的确认 UI 预留：种子数据默认未确认
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="candidate_facts", uselist=False)  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserCandidateFacts user_id={self.user_id} atoms={len(self.atoms or [])}>"
