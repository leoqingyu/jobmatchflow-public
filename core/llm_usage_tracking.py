"""Attributes LLM token usage to a user without threading user_id through every
ai/scoring.py / ai/resume_rewrite.py function signature.

A ContextVar set for the duration of a per-user operation (scoring a job,
generating a resume, ...); ai/providers/*.py read it when logging usage after
each call. Safe to nest — Token-based reset stacks correctly across e.g.
_generate_now -> AssetGenerationService -> ResumeGenerationService.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from core.logger import get_logger

logger = get_logger(__name__)

_current_user_id: ContextVar[int | None] = ContextVar("llm_usage_user_id", default=None)


@contextmanager
def llm_usage_context(user_id: int | None) -> Iterator[None]:
    token = _current_user_id.set(user_id)
    try:
        yield
    finally:
        _current_user_id.reset(token)


def log_llm_usage(
    *,
    task_name: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    """Best-effort: a logging failure must never break the LLM call it's
    attached to, so exceptions are caught and logged, not raised."""
    from db.models import LlmUsageLog
    from db.session import get_db

    try:
        with get_db() as db:
            db.add(
                LlmUsageLog(
                    user_id=_current_user_id.get(),
                    task_name=task_name,
                    model_name=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=prompt_tokens + completion_tokens,
                )
            )
    except Exception as e:
        logger.error("Failed to log LLM usage task=%s model=%s: %s", task_name, model_name, e)
