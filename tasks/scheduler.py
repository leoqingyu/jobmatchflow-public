from core.logger import get_logger

logger = get_logger(__name__)


def start_scheduler():
    """
    启动定时任务调度器：瑞士六个热门州分 3 个时段各搜一个领域、卢森堡每日、
    尾部 20 州按周轮转，外加每周固定时间的投递跟进提醒。均为系统级配置，
    不依赖任何用户的 Settings。

    打分/生成不再是常驻进程（scripts/run_matching_worker.py 保留代码但不再例行运行）——
    Gemini JD 提取（job 级）走 Batch API，DeepSeek 逐项匹配没有 batch 接口、只能靠错开
    北京时间高峰时段，两条节奏都在这里挂载，见 tasks/score_tasks.py。

    当前使用 APScheduler，未来可替换为 Celery/RQ。
    """
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler  # type: ignore
        from apscheduler.triggers.cron import CronTrigger  # type: ignore
        from apscheduler.events import EVENT_JOB_ERROR  # type: ignore
    except ImportError as e:
        raise ImportError("APScheduler 未安装，请运行 pip install apscheduler") from e

    from core.alerts import alert_task_failure
    from tasks.fetch_tasks import (
        run_ch_hot_canton_slot_task,
        run_ch_tail_weekday_slot_task,
        run_lu_daily_slot_task,
    )

    def _run_application_followups() -> None:
        from db.session import get_db
        from services.notification_service import NotificationService
        with get_db() as db:
            NotificationService(db).notify_stale_applications()

    scheduler = BlockingScheduler()

    def _on_job_error(event) -> None:
        """
        兜底：各任务函数内部已经 try/except + 告警，理论上不会再有异常冒到这里；
        万一真的漏了（比如任务函数本身之外的调度层错误），这里再兜一次，避免真正静默。
        """
        alert_task_failure(f"scheduler_job:{event.job_id}", event.exception)

    scheduler.add_listener(_on_job_error, EVENT_JOB_ERROR)

    jobs = [
        ("ch_hot6_tech", 0, lambda: run_ch_hot_canton_slot_task("tech")),
        ("lu_daily", 4, lambda: run_lu_daily_slot_task()),
        ("ch_hot6_finance", 8, lambda: run_ch_hot_canton_slot_task("finance")),
        ("ch_tail_weekday", 12, lambda: run_ch_tail_weekday_slot_task()),
        ("ch_hot6_cross", 16, lambda: run_ch_hot_canton_slot_task("cross")),
        ("application_followups", 9, _run_application_followups),
    ]
    for job_id, hour, func in jobs:
        scheduler.add_job(
            func=func,
            trigger=CronTrigger(hour=hour, minute=0),
            id=job_id,
            replace_existing=True,
        )

    from tasks.score_tasks import run_deepseek_matching_batch, run_gemini_jd_batch_cycle

    # Gemini JD 提取（job 级，Batch API）：没有高峰/非高峰概念，按自己的节奏走，不用等
    # DeepSeek 那边的时段。
    scheduler.add_job(
        func=run_gemini_jd_batch_cycle,
        trigger=CronTrigger(minute="*/30"),
        id="gemini_jd_batch_cycle",
        replace_existing=True,
    )

    # DeepSeek 逐项匹配：没有 batch 接口，只能靠错开北京时间高峰（9-12、14-18，双倍价）。
    # 03:00 / 18:30 都明确落在非高峰区间；显式传 timezone="Asia/Shanghai"，无论宿主机
    # 自己的时区/夏令时怎么变，APScheduler 都会按北京时间正确换算触发时刻——不要在别处
    # 用宿主机本地小时数手算，那样夏令时/冬令时切换时会算错。
    scheduler.add_job(
        func=run_deepseek_matching_batch,
        trigger=CronTrigger(hour=3, minute=0, timezone="Asia/Shanghai"),
        id="deepseek_matching_batch_am",
        replace_existing=True,
    )
    scheduler.add_job(
        func=run_deepseek_matching_batch,
        trigger=CronTrigger(hour=18, minute=30, timezone="Asia/Shanghai"),
        id="deepseek_matching_batch_pm",
        replace_existing=True,
    )

    logger.info("定时任务启动: %s", ", ".join(f"{jid}@{h}:00" for jid, h, _ in jobs))
    logger.info("Gemini JD batch 每 30 分钟一次；DeepSeek 匹配批次北京时间 03:00 / 18:30")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("定时任务已停止")
