from datetime import datetime

from sqlalchemy import Integer, String, Float, Text, JSON, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class UserJobScore(Base):
    __tablename__ = "user_job_scores"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "job_id", "llm_model",
            name="uq_user_job_scores_user_job_model",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), nullable=False)
    # 衔接生成链路用（简历/求职信生成读这两个字段选 CV），由打分 Step 5 产出，跟 Step 1-4
    # 的匹配逻辑本身解耦。用户没有可用 Master CV 时两者都可以是 NULL——打分不再要求
    # "必须先有可用简历"，读取方（asset_service/tracking_service/jobs_list_data 等）已确认
    # 都能安全处理 NULL，不要"看起来像 bug"就改回强制非空
    master_cv_id: Mapped[int | None] = mapped_column(ForeignKey("user_master_cvs.id"), nullable=True)
    recommended_cv_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_master_cvs.id"), nullable=True
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    decision: Mapped[str] = mapped_column(String(50), nullable=False)
    # 打分 Step 3 输出的整体一句话总结（非逐条），代码不生成
    reason_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 逐条 requirement 的匹配结果：[{requirement_id, category, importance, weight, match_level,
    # match_value, evidence_ids, reason, confidence}, ...]；既是"为什么匹配/差在哪"的展示素材，
    # 也是后续简历改写/面试准备复用的证据来源。见 ai/scoring_rules.py::compute_final_score
    requirement_matches: Mapped[list] = mapped_column(JSON, default=list)
    # 触发 70 分封顶的硬性条件 requirement_id 列表（work_authorization/language/certification
    # 三类里被判定为 "none" 的那些），没触发就是空列表
    hard_constraints_hit: Mapped[list] = mapped_column(JSON, default=list)
    # 算分聚合过程：{weighted_score, sum_weight, cap_applied, final_score}，供以后调权重/阈值回看
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    llm_model: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="job_scores")  # noqa: F821
    job: Mapped["Job"] = relationship(back_populates="scores")  # noqa: F821

    def __repr__(self) -> str:
        return f"<UserJobScore user={self.user_id} job={self.job_id} score={self.score}>"
