"""Admin-set per-user quotas (UserProfile.max_matched_jobs / max_generated_resumes /
max_generated_cover_letters — set via PUT /api/v1/admin/users/{id}/quota). All counts
are computed on read against existing tables, no denormalized counters — matches
this codebase's existing style (see services/user_cv_lookup.py)."""

from __future__ import annotations

from typing import Literal

from sqlalchemy.orm import Session

from core.constants import AssetType, ScoringDecision
from db.models import GeneratedAsset, UserJobScore, UserProfile

GenerationKind = Literal["resume", "cover_letter"]

_ASSET_TYPE_BY_KIND: dict[str, str] = {
    "resume": AssetType.RESUME_JSON.value,
    "cover_letter": AssetType.MOTIVATION_LETTER.value,
}


def _quota_limit(db: Session, user_id: int, field: str) -> int | None:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    return getattr(profile, field, None) if profile else None


def matched_jobs_count(db: Session, user_id: int) -> int:
    return (
        db.query(UserJobScore)
        .filter(UserJobScore.user_id == user_id, UserJobScore.decision != ScoringDecision.DISCARD.value)
        .count()
    )


def match_quota_remaining(db: Session, user_id: int) -> bool:
    """True：还没设额度，或还没到；False：已达到 max_matched_jobs，应停止继续打分。"""
    limit = _quota_limit(db, user_id, "max_matched_jobs")
    if limit is None:
        return True
    return matched_jobs_count(db, user_id) < limit


def generated_asset_count(db: Session, user_id: int, kind: GenerationKind) -> int:
    return (
        db.query(GeneratedAsset)
        .filter(GeneratedAsset.user_id == user_id, GeneratedAsset.asset_type == _ASSET_TYPE_BY_KIND[kind])
        .count()
    )


def check_generation_quota(db: Session, user_id: int, kind: GenerationKind) -> bool:
    """True：还没设额度，或还没到，可以继续生成；False：已达到对应额度上限。"""
    field = "max_generated_resumes" if kind == "resume" else "max_generated_cover_letters"
    limit = _quota_limit(db, user_id, field)
    if limit is None:
        return True
    return generated_asset_count(db, user_id, kind) < limit
