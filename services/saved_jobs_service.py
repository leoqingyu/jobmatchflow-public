"""用户收藏关注的岗位——跟投递跟进（ApplicationTracking）完全独立，见 db.models.UserSavedJob。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from core.logger import get_logger
from db.models import Job, UserJobScore, UserSavedJob

logger = get_logger(__name__)


class SavedJobsService:
    def __init__(self, db: Session):
        self.db = db

    def save_job(self, user_id: int, job_id: int) -> UserSavedJob:
        existing = self._get(user_id, job_id)
        if existing:
            return existing
        record = UserSavedJob(user_id=user_id, job_id=job_id)
        try:
            self.db.add(record)
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            existing = self._get(user_id, job_id)
            if existing:
                return existing
            raise
        return record

    def unsave_job(self, user_id: int, job_id: int) -> None:
        record = self._get(user_id, job_id)
        if record:
            self.db.delete(record)
            self.db.flush()

    def list_saved_job_ids(self, user_id: int) -> set[int]:
        rows = self.db.query(UserSavedJob.job_id).filter(UserSavedJob.user_id == user_id).all()
        return {int(r[0]) for r in rows}

    def list_saved_jobs(self, user_id: int) -> list[dict]:
        rows = (
            self.db.query(UserSavedJob, Job, UserJobScore)
            .join(Job, Job.id == UserSavedJob.job_id)
            .outerjoin(
                UserJobScore,
                (UserJobScore.job_id == UserSavedJob.job_id) & (UserJobScore.user_id == user_id),
            )
            .filter(UserSavedJob.user_id == user_id)
            .order_by(UserSavedJob.created_at.desc())
            .all()
        )
        return [
            {
                "job_id": job.id,
                "title": job.title,
                "company": job.company,
                "country": job.country,
                "score": float(score.score) if score else None,
                "decision": score.decision if score else None,
                "saved_at": saved.created_at.isoformat() if saved.created_at else None,
            }
            for saved, job, score in rows
        ]

    def _get(self, user_id: int, job_id: int) -> UserSavedJob | None:
        return (
            self.db.query(UserSavedJob)
            .filter(UserSavedJob.user_id == user_id, UserSavedJob.job_id == job_id)
            .first()
        )
