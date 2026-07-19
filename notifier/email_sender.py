from core.config import settings
from core.logger import get_logger

logger = get_logger(__name__)


def send_job_digest(recipient: str, jobs: list) -> bool:
    """
    发送高分岗位摘要邮件。
    优先使用 Resend API，fallback 到 SMTP。
    """
    if not settings.resend_api_key:
        logger.warning("未配置 RESEND_API_KEY，跳过邮件发送")
        return False


def send_followup_reminders(recipient: str, jobs: list) -> bool:
    """提醒超过两周未更新的投递。"""
    if not settings.resend_api_key:
        logger.warning("未配置 RESEND_API_KEY，跳过跟进提醒")
        return False
    try:
        import resend  # type: ignore
        resend.api_key = settings.resend_api_key
        rows = "".join(f"<li>{title} · {company or ''}</li>" for title, company in jobs)
        resend.Emails.send({"from": settings.notification_email_from, "to": [recipient],
            "subject": "[JobMatchFlow] 要不要跟进这些投递？",
            "html": f"<h2>两周没有更新的投递</h2><ul>{rows}</ul><p>登录 JobMatchFlow 更新状态或发送 follow-up。</p>"})
        return True
    except Exception as e:
        logger.error(f"跟进提醒发送失败: {e}")
        return False

    try:
        import resend  # type: ignore
        resend.api_key = settings.resend_api_key

        subject, html = _build_digest_email(jobs)

        resend.Emails.send({
            "from": settings.notification_email_from,
            "to": [recipient],
            "subject": subject,
            "html": html,
        })
        logger.info(f"邮件发送成功: {recipient}, {len(jobs)} 条岗位")
        return True

    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


def _build_digest_email(jobs: list) -> tuple[str, str]:
    count = len(jobs)
    subject = f"[JobMatchFlow] {count} 个高匹配岗位需要关注"

    rows = ""
    for s in jobs:
        job = getattr(s, "job", None)
        title = job.title if job else "N/A"
        company = job.company if job else "N/A"
        score = s.score
        rows += f"<tr><td>{title}</td><td>{company}</td><td><b>{score}</b></td></tr>"

    tg = settings.score_threshold_generate
    html = f"""
<h2>JobMatchFlow 高匹配岗位报告</h2>
<p>以下 {count} 个岗位匹配度 ≥ {tg}（当前 generate 阈值），已自动生成定制材料：</p>
<table border="1" cellpadding="8" style="border-collapse:collapse;">
  <thead>
    <tr><th>职位</th><th>公司</th><th>匹配分</th></tr>
  </thead>
  <tbody>{rows}</tbody>
</table>
<p>请登录 JobMatchFlow 查看详情和生成材料。</p>
"""
    return subject, html


def send_verification_email(recipient: str, code: str) -> bool:
    """Signup email-verification code. See api/web_routes.py::api_signup."""
    if not settings.resend_api_key:
        logger.warning("未配置 RESEND_API_KEY，跳过验证邮件发送")
        return False
    try:
        import resend  # type: ignore
        resend.api_key = settings.resend_api_key
        resend.Emails.send({
            "from": settings.notification_email_from,
            "to": [recipient],
            "subject": "Verify your JobMatchFlow email",
            "html": f"<h2>Verify your email</h2><p>Your verification code is:</p>"
                    f"<p style=\"font-size:24px;font-weight:bold;letter-spacing:4px;\">{code}</p>"
                    f"<p>This code expires in 15 minutes. If you didn't request this, you can ignore this email.</p>",
        })
        return True
    except Exception as e:
        logger.error(f"验证邮件发送失败: {e}")
        return False
