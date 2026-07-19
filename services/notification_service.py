from sqlalchemy.orm import Session

from core.logger import get_logger
from core.constants import ScoringDecision, NotificationChannel, NotificationStatus, ApplicationStatus
from datetime import datetime, timedelta, timezone
from db.models import UserJobScore, Job, Notification, UserProfile

logger = get_logger(__name__)


class NotificationService:
    def __init__(self, db: Session):
        self.db = db

    def notify_new_high_score_jobs(self, user_id: int) -> dict:
        """对新的高分岗位发送邮件通知"""
        pending = self._get_pending_notifications(user_id)
        if not pending:
            return {"sent": 0}

        profile = self.db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
        recipient = profile.notification_email if profile else None
        if not recipient:
            logger.warning(f"用户 {user_id} 没有配置通知邮箱，跳过通知")
            return {"sent": 0, "skipped": len(pending)}

        from notifier.email_sender import send_job_digest
        sent = send_job_digest(recipient=recipient, jobs=pending)

        for score in pending:
            notif = Notification(
                user_id=user_id,
                job_id=score.job_id,
                notification_type="high_score_job",
                channel=NotificationChannel.EMAIL.value,
                recipient=recipient,
                payload_summary=f"score={score.score}",
                status=NotificationStatus.SENT.value if sent else NotificationStatus.FAILED.value,
            )
            self.db.add(notif)

        logger.info(f"通知发送完成: {len(pending)} 条")
        return {"sent": len(pending) if sent else 0}

    def _get_pending_notifications(self, user_id: int) -> list[UserJobScore]:
        already_notified_job_ids = (
            self.db.query(Notification.job_id)
            .filter(Notification.user_id == user_id, Notification.status == NotificationStatus.SENT.value)
            .scalar_subquery()
        )
        return (
            self.db.query(UserJobScore)
            .filter(
                UserJobScore.user_id == user_id,
                UserJobScore.decision == ScoringDecision.GENERATE.value,
                UserJobScore.job_id.not_in(already_notified_job_ids),
            )
            .all()
        )

    def notify_stale_applications(self, user_id: int | None = None) -> dict:
        """Applied/Interview 超过 14 天未更新时（未到终态 offer/rejected），每两周提醒一次。"""
        from db.models import ApplicationTracking
        from notifier.email_sender import send_followup_reminders
        users = [user_id] if user_id else [x[0] for x in self.db.query(ApplicationTracking.user_id).distinct()]
        result = {"sent": 0}
        cutoff = datetime.now(timezone.utc) - timedelta(days=14)
        for uid in users:
            profile = self.db.query(UserProfile).filter(UserProfile.user_id == uid).first()
            if not profile or not profile.notification_email:
                continue
            stale = (self.db.query(ApplicationTracking, Job).join(Job, ApplicationTracking.job_id == Job.id)
                .filter(ApplicationTracking.user_id == uid,
                    ApplicationTracking.application_status.in_([ApplicationStatus.APPLIED.value, ApplicationStatus.INTERVIEW.value]),
                    ApplicationTracking.last_stage_at < cutoff).all())
            already = {n.job_id for n in self.db.query(Notification).filter(Notification.user_id == uid,
                Notification.notification_type == "stale_application", Notification.created_at >= cutoff).all()}
            stale = [(tr, job) for tr, job in stale if job.id not in already]
            if not stale:
                continue
            ok = send_followup_reminders(profile.notification_email, [(j.title, j.company) for _, j in stale])
            for tr, job in stale:
                self.db.add(Notification(user_id=uid, job_id=job.id, notification_type="stale_application",
                    channel=NotificationChannel.EMAIL.value, recipient=profile.notification_email,
                    payload_summary="14 days without status update",
                    status=NotificationStatus.SENT.value if ok else NotificationStatus.FAILED.value))
            result["sent"] += len(stale) if ok else 0
        return result
