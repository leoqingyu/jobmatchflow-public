"""Password hashing + verification-code hashing, shared by signup/login/verify-email
(api/web_routes.py)."""

import hashlib
import secrets

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))


def generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_verification_code(user_id: int, code: str) -> str:
    """Scoped by user_id so two users independently drawing the same 6-digit code
    can't collide on email_verifications.token_hash's unique constraint."""
    return hashlib.sha256(f"{user_id}:{code}".encode("utf-8")).hexdigest()
