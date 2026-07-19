import logging
import sys
from typing import Optional


def get_logger(name: str, level: Optional[int] = None) -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level or logging.INFO)
    logger.propagate = False

    return logger


def get_task_logger(
    task_name: str,
    user_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> logging.LoggerAdapter:
    logger = get_logger(f"jobmatchflow.task.{task_name}")
    extra = {
        "task_name": task_name,
        "user_id": user_id,
        "job_id": job_id,
    }
    return logging.LoggerAdapter(logger, extra)
