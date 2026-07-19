"""Session auth: httpOnly JWT cookie + ownership/role dependencies.

The cookie payload only carries {"sub": user_id, "iat", "exp"} — role and status
are always re-read from the DB on every request, so a ban/role change takes
effect on the very next request rather than waiting for the JWT to expire.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, Path as PathParam, Request, Response

from core.config import settings
from db.models import User
from db.session import get_db

SESSION_COOKIE_NAME = "jmf_session"
_JWT_ALGORITHM = "HS256"


def create_session_token(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        # PyJWT requires the registered "sub" claim to be a string (RFC 7519).
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_expires_days),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_JWT_ALGORITHM)


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=create_session_token(user_id),
        httponly=True,
        samesite="lax",
        secure=settings.environment == "production",
        max_age=settings.jwt_expires_days * 24 * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def get_current_user(request: Request, response: Response) -> User:
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[_JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Not authenticated")

    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub.isdigit():
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_id = int(sub)

    with get_db() as db:
        user = db.get(User, user_id)
        if not user or user.status != "active":
            raise HTTPException(status_code=401, detail="Not authenticated")
        db.expunge(user)

    # Sliding renewal: reissue once past the halfway point of the token's
    # lifetime, so an active user's session never expires mid-use.
    issued_at = payload.get("iat")
    if isinstance(issued_at, int):
        age_sec = datetime.now(timezone.utc).timestamp() - issued_at
        half_life_sec = settings.jwt_expires_days * 24 * 3600 / 2
        if age_sec > half_life_sec:
            set_session_cookie(response, user.id)

    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    return current_user


def require_owner_or_admin(
    user_id: int = PathParam(..., ge=1),
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != "admin" and current_user.id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    return current_user
