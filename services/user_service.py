from sqlalchemy.orm import Session

from core.logger import get_logger
from core.exceptions import UserNotFoundError
from db.models import User

logger = get_logger(__name__)


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def get_or_create_default_user(self, email: str, name: str) -> User:
        """获取或创建默认用户（单用户模式使用）"""
        user = self.db.query(User).filter(User.email == email).first()
        if not user:
            user = User(email=email, name=name, status="active")
            self.db.add(user)
            self.db.flush()
            logger.info(f"创建用户: {email}")
        return user

    def get_by_id(self, user_id: int) -> User:
        user = self.db.get(User, user_id)
        if not user:
            raise UserNotFoundError(f"用户不存在: {user_id}")
        return user

    def list_all(self) -> list[User]:
        return self.db.query(User).filter(User.status == "active").all()

    def list_all_for_admin(self) -> list[User]:
        """Unlike list_all(), includes non-active accounts (pending verification,
        deleted) — the admin console needs to see the full picture, not just who's
        currently usable."""
        return self.db.query(User).order_by(User.id.asc()).all()
