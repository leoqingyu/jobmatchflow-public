from datetime import datetime

from sqlalchemy import String, Text, DateTime, JSON, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source", "external_job_id", name="uq_jobs_source_external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    external_job_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str | None] = mapped_column(String(100), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_clean: Mapped[str | None] = mapped_column(Text, nullable=True)
    date_posted: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 完整 JD 清洗后的 SHA-256，用于跨 external_job_id 的高可信重复判断
    jd_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    # JD 原子要求提取结果缓存（ai.scoring.extract_jd_requirements 的输出，{"requirements": [...]}）；
    # job 级，全体用户共用，description 变了（content_hash 变化）要清空重算，见
    # ingestion_service._upsert_job
    structured_requirements: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # 打分 Step 1 向量初筛用的岗位向量（标题+JD 片段 embedding），job 级缓存，全体用户复用；
    # 见 ai/direction_matching.py
    title_embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 粗领域桶（core.constants.JobDomain 之一），随 structured_requirements 一起由 Step 2
    # 顺带产出，job 级缓存。改动前已打过分的旧岗位这里是 NULL，简历选材偏好聚合时按
    # "unknown" 桶处理，不做迁移补算。
    domain: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 用工类型（core.constants.EmploymentType：internship/full_time），随 structured_requirements
    # 一起由 Step 2 顺带产出，job 级缓存。改动前已缓存过 structured_requirements 的旧岗位这里
    # 是 NULL（缓存命中直接返回，不会补跑 LLM 重新分类）；NULL 时实习/全职过滤永不触发，
    # 不误伤旧岗位（见 ai.scoring_rules.employment_type_mismatch）。
    employment_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Gemini Batch API 队列（services/gemini_jd_batch_service.py）：某用户 Step 1 通过、但
    # structured_requirements 还没抽取时第一次置位；提交进一个 batch 后 jd_extraction_batch_id
    # 跟着置位，batch 失败时清回 NULL 以便下一轮重新扫到。见 services/scoring_service.py::
    # _ensure_structured_requirements。
    jd_extraction_queued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    jd_extraction_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("gemini_jd_batches.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # relationships
    scores: Mapped[list["UserJobScore"]] = relationship(back_populates="job")  # noqa: F821
    generated_assets: Mapped[list["GeneratedAsset"]] = relationship(back_populates="job")  # noqa: F821
    application_trackings: Mapped[list["ApplicationTracking"]] = relationship(back_populates="job")  # noqa: F821
    notifications: Mapped[list["Notification"]] = relationship(back_populates="job")  # noqa: F821
    jd_extraction_batch: Mapped["GeminiJdBatch"] = relationship(back_populates="jobs")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Job id={self.id} title={self.title} company={self.company}>"
