"""
打分 + 生成长驻进程——**已不再例行运行**，保留代码仅供手动调试（见
scripts/run_matching_worker.py 顶部说明）。DeepSeek 引入峰谷定价后，这里"多用户公平轮转、
逐条穿插处理"的模式正好踩中两个问题：不分时段持续调用 DeepSeek（高峰期双倍价也照打），
且穿插处理导致同一用户的请求不连续、命中不了 DeepSeek 的上下文缓存。例行打分现在拆到
tasks/score_tasks.py：Gemini JD 提取走 Batch API（不分时段，按自己节奏跑），DeepSeek
逐项匹配走北京时间非高峰批次、每个用户连续处理完再换下一个。

按 (user, job) 逐条处理，不是"这个用户所有待打分岗位一次扫完"：轮流处理每个满足打分条件的
用户各一条，处理完随机等一下再处理下一个用户的下一条，这样多个用户之间是公平轮转，不会有人
排后面永远轮不到。一整轮下来所有用户都没有活干，才进入长睡、顺带把过期岗位标记也做了
（反正进程一直醒着，不用为这一步单独开 cron）。
"""

from __future__ import annotations

import random
import time

from core.config import settings
from core.logger import get_logger
from db.session import get_db
from services.ingestion_service import JobIngestionService
from services.scoring_service import JobScoringService
from services.user_cv_lookup import list_user_ids_ready_for_scoring

logger = get_logger(__name__)


def _delay() -> float:
    lo = float(settings.matching_worker_delay_min_sec)
    hi = float(settings.matching_worker_delay_max_sec)
    if hi <= lo:
        return lo
    return random.uniform(lo, hi)


def run_matching_worker_forever(*, idle_poll_sec: float | None = None) -> None:
    idle_sec = float(idle_poll_sec if idle_poll_sec is not None else settings.matching_worker_idle_poll_sec)
    processed = 0
    scored = 0
    rejected = 0
    errors = 0

    logger.info(
        "打分+生成常驻进程启动，随机等待 %s~%ss，全员无待处理岗位时每 %.0fs 重查一次",
        settings.matching_worker_delay_min_sec,
        settings.matching_worker_delay_max_sec,
        idle_sec,
    )

    while True:
        with get_db() as db:
            user_ids = list_user_ids_ready_for_scoring(db)

        if not user_ids:
            logger.info("打分+生成常驻进程: 没有满足打分条件的用户，%.0fs 后重查", idle_sec)
            time.sleep(idle_sec)
            continue

        did_work = False
        for uid in user_ids:
            with get_db() as db:
                try:
                    result = JobScoringService(db).score_and_generate_next_job_for_user(uid)
                except Exception as e:
                    logger.exception("打分+生成常驻进程: user_id=%s 处理异常，跳过继续: %s", uid, e)
                    result = {"job_id": None, "outcome": "error"}
                    errors += 1

            if result is None:
                continue

            did_work = True
            processed += 1
            outcome = result["outcome"]
            if outcome == "scored":
                scored += 1
            elif outcome == "rejected":
                rejected += 1
            elif outcome == "error":
                errors += 1
            logger.info(
                "打分+生成常驻进程: user_id=%s job_id=%s outcome=%s（累计 scored=%s rejected=%s error=%s）",
                uid, result.get("job_id"), outcome, scored, rejected, errors,
            )
            time.sleep(_delay())

        if not did_work:
            with get_db() as db:
                expired = JobIngestionService(db).expire_stale_jobs()
            if expired:
                logger.info("打分+生成常驻进程: 顺带标记过期岗位 %s 条", expired)
            logger.info(
                "打分+生成常驻进程: 本轮所有用户都暂无待处理岗位，%.0fs 后重查", idle_sec
            )
            time.sleep(idle_sec)
