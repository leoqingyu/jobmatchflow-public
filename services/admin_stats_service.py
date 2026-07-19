"""Admin console read-only stats: per-user activity/cost, and a system-wide
overview. Costs are computed at query time from raw token counts (see
core/llm_pricing.py) — never stored, so a pricing change doesn't require
rewriting historical rows."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.constants import AssetType, ScoringDecision
from core.llm_pricing import estimate_cost_usd
from db.models import GeneratedAsset, LlmUsageLog, User, UserJobScore


def _user_cost_and_tokens(db: Session, user_id: int | None) -> tuple[float, int]:
    rows = (
        db.query(LlmUsageLog.model_name, LlmUsageLog.prompt_tokens, LlmUsageLog.completion_tokens)
        .filter(LlmUsageLog.user_id == user_id)
        .all()
    )
    total_cost = sum(estimate_cost_usd(r.model_name, r.prompt_tokens, r.completion_tokens) for r in rows)
    total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in rows)
    return round(total_cost, 4), total_tokens


def user_stats(db: Session, user_id: int) -> dict:
    user = db.get(User, user_id)
    match_count = (
        db.query(UserJobScore)
        .filter(UserJobScore.user_id == user_id, UserJobScore.decision != ScoringDecision.DISCARD.value)
        .count()
    )
    resume_count = (
        db.query(GeneratedAsset)
        .filter(GeneratedAsset.user_id == user_id, GeneratedAsset.asset_type == AssetType.RESUME_JSON.value)
        .count()
    )
    cover_letter_count = (
        db.query(GeneratedAsset)
        .filter(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.asset_type == AssetType.MOTIVATION_LETTER.value,
        )
        .count()
    )
    estimated_cost_usd, total_tokens = _user_cost_and_tokens(db, user_id)
    last_usage_at = (
        db.query(func.max(LlmUsageLog.created_at)).filter(LlmUsageLog.user_id == user_id).scalar()
    )
    last_active_at = max(
        (t for t in (user.last_login_at if user else None, last_usage_at) if t is not None),
        default=None,
    )
    return {
        "user_id": user_id,
        "match_count": match_count,
        "resume_count": resume_count,
        "cover_letter_count": cover_letter_count,
        "estimated_cost_usd": estimated_cost_usd,
        "total_tokens": total_tokens,
        "last_active_at": last_active_at,
    }


def overview_stats(db: Session) -> dict:
    total_users = db.query(User).filter(User.status == "active").count()
    total_matches = (
        db.query(UserJobScore).filter(UserJobScore.decision != ScoringDecision.DISCARD.value).count()
    )
    total_resumes = (
        db.query(GeneratedAsset).filter(GeneratedAsset.asset_type == AssetType.RESUME_JSON.value).count()
    )
    total_cover_letters = (
        db.query(GeneratedAsset)
        .filter(GeneratedAsset.asset_type == AssetType.MOTIVATION_LETTER.value)
        .count()
    )
    rows = db.query(LlmUsageLog.model_name, LlmUsageLog.prompt_tokens, LlmUsageLog.completion_tokens).all()
    total_cost = sum(estimate_cost_usd(r.model_name, r.prompt_tokens, r.completion_tokens) for r in rows)
    total_tokens = sum(r.prompt_tokens + r.completion_tokens for r in rows)
    return {
        "total_users": total_users,
        "total_matches": total_matches,
        "total_resumes": total_resumes,
        "total_cover_letters": total_cover_letters,
        "estimated_cost_usd": round(total_cost, 4),
        "total_tokens": total_tokens,
    }
