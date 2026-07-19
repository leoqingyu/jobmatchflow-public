from datetime import datetime

from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active")
    # Bcrypt hash (never plaintext); nullable because pre-auth demo accounts have
    # none until they set a real password via signup/reset.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # "user" | "admin" — see api/auth_deps.py::require_admin.
    role: Mapped[str] = mapped_column(String(20), default="user")
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    # relationships
    email_verifications: Mapped[list["EmailVerification"]] = relationship(back_populates="user")  # noqa: F821
    profile: Mapped["UserProfile"] = relationship(back_populates="user", uselist=False)  # noqa: F821
    search_profiles: Mapped[list["UserSearchProfile"]] = relationship(back_populates="user")  # noqa: F821
    master_cvs: Mapped[list["UserMasterCV"]] = relationship(back_populates="user")  # noqa: F821
    job_directions: Mapped[list["UserJobDirection"]] = relationship(back_populates="user")  # noqa: F821
    candidate_facts: Mapped["UserCandidateFacts"] = relationship(back_populates="user", uselist=False)  # noqa: F821
    experience_units: Mapped[list["UserExperienceUnit"]] = relationship(back_populates="user")  # noqa: F821
    experience_preference_events: Mapped[list["UserExperiencePreferenceEvent"]] = relationship(back_populates="user")  # noqa: F821
    job_scores: Mapped[list["UserJobScore"]] = relationship(back_populates="user")  # noqa: F821
    generated_assets: Mapped[list["GeneratedAsset"]] = relationship(back_populates="user")  # noqa: F821
    application_trackings: Mapped[list["ApplicationTracking"]] = relationship(back_populates="user")  # noqa: F821
    notifications: Mapped[list["Notification"]] = relationship(back_populates="user")  # noqa: F821
    pipeline_runs: Mapped[list["PipelineRun"]] = relationship(back_populates="user")  # noqa: F821
    llm_usage_logs: Mapped[list["LlmUsageLog"]] = relationship(back_populates="user")  # noqa: F821

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email}>"
