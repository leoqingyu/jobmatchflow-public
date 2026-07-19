from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserExperienceUnit(Base):
    """
    半结构化经历单元：一段工作/项目/课设一条记录，取代旧流程里整份 Master CV 纯文本作为
    打分输入。只存"在什么背景下、做了什么、用了什么、负责到什么程度、产生了什么结果"这些
    事实，不预先让 LLM 抽象出能力清单——能力判断留到打分 Step 3 见到具体 JD 后再做。

    technologies 是这段经历里提到的原始表述（未做别名归一化），和
    UserCandidateFacts.atoms 里归一化后的 skill atom 是两份不同用途的数据：前者是证据原文，
    后者是归一化事实，打分 prompt 里要向模型讲清楚这个分工。
    """

    __tablename__ = "user_experience_units"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    employer: Mapped[str | None] = mapped_column(String(255), nullable=True)
    background: Mapped[str | None] = mapped_column(Text, nullable=True)
    actions: Mapped[str | None] = mapped_column(Text, nullable=True)
    technologies: Mapped[list] = mapped_column(JSON, default=list)
    # "independent"（独立交付） | "participant"（参与）
    ownership: Mapped[str | None] = mapped_column(String(20), nullable=True)
    results: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # 原始日期表述（如 "2021" / "2021-03 至今"），日期缺失月份时 start_date/end_date 按当月 1 号处理
    raw_date_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    # 用户一次性标注的含金量粗档："flagship"|"solid"|"filler"，见 core.constants.ExperienceTier。
    # 简历选材优先级 = 相关性 × tier 权重 × 历史偏好修正，见 ai/resume_selection.py
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="experience_units")  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserExperienceUnit id={self.id} user_id={self.user_id} title={self.title}>"
