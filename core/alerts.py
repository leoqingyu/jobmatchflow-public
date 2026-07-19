"""
运维告警：定时抓取/下游任务失败时发邮件通知，复用现有 Resend 配置
（RESEND_API_KEY / NOTIFICATION_EMAIL_FROM），收件地址用 NOTIFICATION_EMAIL_TO
（管理员邮箱，与用户端各自配置的 profile.notification_email 无关）。

未配置 Resend 或收件地址时，只记错误日志，不抛异常——告警本身失败不该掩盖原始错误，
调用方（tasks/fetch_tasks.py）永远看得到日志里的完整堆栈。
"""

from __future__ import annotations

from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)


def send_ops_alert(subject: str, body: str) -> None:
    if not settings.resend_api_key or not settings.notification_email_to:
        logger.warning(
            "未配置 RESEND_API_KEY / NOTIFICATION_EMAIL_TO，告警邮件跳过（仅记日志）: %s",
            subject,
        )
        return
    try:
        import resend  # type: ignore

        resend.api_key = settings.resend_api_key
        resend.Emails.send(
            {
                "from": settings.notification_email_from,
                "to": [settings.notification_email_to],
                "subject": subject,
                "html": f"<pre>{body}</pre>",
            }
        )
        logger.info("告警邮件已发送: %s", subject)
    except Exception as e:
        logger.error("告警邮件发送失败（不影响原始错误已记录的日志）: %s", e)


def alert_task_failure(step: str, error: BaseException) -> None:
    """记完整堆栈日志 + 发告警邮件，标明具体是哪个步骤失败。"""
    logger.error("任务步骤失败: step=%s error=%r", step, error, exc_info=error)
    send_ops_alert(
        subject=f"[JobMatchFlow] 任务失败: {step}",
        body=f"步骤：{step}\n错误：{error!r}\n\n完整堆栈见服务端日志。",
    )
