from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserJobDirection(Base):
    """
    用户求职方向：一次性设置，之后所有岗位打分免费复用（见 ai/direction_matching.py Step 1）。

    expanded_text/embedding 由便宜模型对 title 扩写同义/近义职位名后一次性生成；title 改了
    才需要重新扩写+embed（见 ExperienceLibraryService 里对应的更新逻辑）。
    """

    __tablename__ = "user_job_directions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # 只存英文扩写版本供调试/API 展示；三语版本本身不落单独字段，只用于下面 embedding
    expanded_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 三语（英/德/法）L2 归一化向量的列表，list[list[float]]，而不是单个向量——瑞士岗位
    # 大量是德语/法语原文，见 ai/direction_matching.py::load_active_direction_vectors 的说明。
    # 点积即余弦相似度；classify() 对这些向量取 max，不要求三个语言版本都过阈值。
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 产出该向量的模型名；模型换了旧向量就该视为陈旧、重新生成
    embed_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship(back_populates="job_directions")  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserJobDirection id={self.id} user_id={self.user_id} title={self.title}>"
