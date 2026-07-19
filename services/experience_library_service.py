"""
经历库（求职方向 + 确定性事实 + 半结构化经历单元）的 CRUD + 从已上传 Master CV 一次性
bootstrap 抽取。取代打分场景下的 Master CV 文本输入，但不碰 UserMasterCV 表本身——
简历/求职信生成继续读那张表，这里只是"另一份给打分用的结构化数据"。
"""

from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy.orm import Session

from core.cv_plain import master_cv_plain_text
from core.llm_usage_tracking import llm_usage_context
from core.logger import get_logger
from core.skill_aliases import normalize_skill_label
from ai.llm_client import LLMClient
from ai.scoring import extract_experience_library_from_text
from db.models import UserCandidateFacts, UserExperienceUnit, UserJobDirection
from services.profile_service import ProfileService

logger = get_logger(__name__)


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (text or "").strip().lower()).strip("_")
    return s or "x"


def _assign_atom_ids(atoms: list[dict]) -> list[dict]:
    """技能类 label 先过别名归一化，再按 type_slug(label) 生成稳定 id；撞车加数字后缀兜底。"""
    seen: dict[str, int] = {}
    out = []
    for a in atoms or []:
        atype = a.get("type") or "skill"
        label = a.get("label") or ""
        if atype == "skill":
            label = normalize_skill_label(label)
        base = f"{atype}_{_slugify(label)}"
        seen[base] = seen.get(base, 0) + 1
        aid = base if seen[base] == 1 else f"{base}_{seen[base]}"
        out.append({"id": aid, "type": atype, "label": label, "detail": a.get("detail") or {}})
    return out


def _to_date(year, month) -> date | None:
    if not year:
        return None
    try:
        y = int(year)
        m = int(month) if month else 1
        m = min(max(m, 1), 12)
        return date(y, m, 1)
    except (TypeError, ValueError):
        return None


class ExperienceLibraryService:
    # ------------------------------------------------------------------
    # 求职方向
    # ------------------------------------------------------------------
    @staticmethod
    def list_directions(db: Session, user_id: int) -> list[UserJobDirection]:
        return (
            db.query(UserJobDirection)
            .filter(UserJobDirection.user_id == user_id)
            .order_by(UserJobDirection.id.asc())
            .all()
        )

    @staticmethod
    def create_direction(db: Session, user_id: int, title: str) -> UserJobDirection:
        """
        只存标题，不算向量。向量算的是原始 title 没意义——扩写就是为了把干巴巴的职位名
        变成更利于向量匹配的近义表述，没扩写过就去 embed 等于绕过了扩写直接拿一个注定
        很差的向量，那还不如没有。向量只在 ai/direction_matching.py::ensure_direction_expansion
        里、LLM 扩写成功之后才第一次算；扩写没成功之前这个方向的 embedding 保持 None，
        load_active_direction_vectors 会自动把它排除在向量匹配之外（不是"参与但很差"，是
        "暂不参与"），不影响保存本身。
        """
        title = (title or "").strip()
        if not title:
            raise ValueError("方向标题不能为空")
        direction = UserJobDirection(
            user_id=user_id,
            title=title,
            expanded_text=None,
            embedding=None,
            embed_model=None,
            is_active=True,
        )
        db.add(direction)
        db.flush()
        return direction

    @staticmethod
    def update_direction(
        db: Session,
        user_id: int,
        direction_id: int,
        *,
        title: str | None = None,
        is_active: bool | None = None,
    ) -> UserJobDirection:
        d = (
            db.query(UserJobDirection)
            .filter(UserJobDirection.id == direction_id, UserJobDirection.user_id == user_id)
            .first()
        )
        if not d:
            raise ValueError("方向不存在")
        if is_active is not None:
            d.is_active = is_active
        new_title = (title or "").strip()
        if new_title and new_title != d.title:
            # 标题变了，旧的扩写文本/向量对不上了：清空，让下次打分时 ensure_direction_expansion
            # 重新扩写、重新 embed；跟 create_direction 同一个原则，不拿未扩写的 title 凑合算向量。
            d.title = new_title
            d.expanded_text = None
            d.embedding = None
            d.embed_model = None
        db.flush()
        return d

    @staticmethod
    def delete_direction(db: Session, user_id: int, direction_id: int) -> None:
        d = (
            db.query(UserJobDirection)
            .filter(UserJobDirection.id == direction_id, UserJobDirection.user_id == user_id)
            .first()
        )
        if d:
            db.delete(d)
            db.flush()

    # ------------------------------------------------------------------
    # 确定性事实
    # ------------------------------------------------------------------
    @staticmethod
    def get_candidate_facts(db: Session, user_id: int) -> UserCandidateFacts | None:
        return db.query(UserCandidateFacts).filter(UserCandidateFacts.user_id == user_id).first()

    @staticmethod
    def upsert_candidate_facts(
        db: Session,
        user_id: int,
        *,
        atoms: list[dict] | None = None,
        total_years_experience: float | None = None,
        source: str | None = None,
        mark_confirmed: bool = True,
    ) -> UserCandidateFacts:
        facts = ExperienceLibraryService.get_candidate_facts(db, user_id)
        if facts is None:
            facts = UserCandidateFacts(user_id=user_id, atoms=[], source=source or "manual")
            db.add(facts)
        if atoms is not None:
            facts.atoms = _assign_atom_ids(atoms)
        if total_years_experience is not None:
            facts.total_years_experience = total_years_experience
        if source is not None:
            facts.source = source
        if mark_confirmed:
            facts.confirmed = True
            facts.confirmed_at = datetime.utcnow()
        db.flush()
        return facts

    # ------------------------------------------------------------------
    # 经历单元
    # ------------------------------------------------------------------
    @staticmethod
    def list_experience_units(db: Session, user_id: int) -> list[UserExperienceUnit]:
        return (
            db.query(UserExperienceUnit)
            .filter(UserExperienceUnit.user_id == user_id)
            .order_by(UserExperienceUnit.order_index.asc(), UserExperienceUnit.id.asc())
            .all()
        )

    @staticmethod
    def create_experience_unit(db: Session, user_id: int, **fields) -> UserExperienceUnit:
        fields.setdefault("source", "manual")
        fields.setdefault("confirmed", True)
        unit = UserExperienceUnit(user_id=user_id, **fields)
        db.add(unit)
        db.flush()
        return unit

    @staticmethod
    def update_experience_unit(
        db: Session, user_id: int, unit_id: int, **fields
    ) -> UserExperienceUnit:
        u = (
            db.query(UserExperienceUnit)
            .filter(UserExperienceUnit.id == unit_id, UserExperienceUnit.user_id == user_id)
            .first()
        )
        if not u:
            raise ValueError("经历单元不存在")
        for k, v in fields.items():
            setattr(u, k, v)
        db.flush()
        return u

    @staticmethod
    def delete_experience_unit(db: Session, user_id: int, unit_id: int) -> None:
        u = (
            db.query(UserExperienceUnit)
            .filter(UserExperienceUnit.id == unit_id, UserExperienceUnit.user_id == user_id)
            .first()
        )
        if u:
            db.delete(u)
            db.flush()

    # ------------------------------------------------------------------
    # Bootstrap：从已上传 Master CV 一次性抽取种子数据
    # ------------------------------------------------------------------
    @staticmethod
    def extract_from_master_cv(db: Session, user_id: int, llm: LLMClient) -> dict:
        with llm_usage_context(user_id):
            return ExperienceLibraryService._extract_from_master_cv_impl(db, user_id, llm)

    @staticmethod
    def _extract_from_master_cv_impl(db: Session, user_id: int, llm: LLMClient) -> dict:
        """
        一次性初始化，不建立持续同步关系：已有经历单元时拒绝重复抽取（避免每次点一下就
        重复灌一批），用户之后手动增删改。
        """
        existing = (
            db.query(UserExperienceUnit).filter(UserExperienceUnit.user_id == user_id).count()
        )
        if existing > 0:
            raise ValueError("经历库已有数据，bootstrap 只做一次性初始化，请通过编辑接口调整")

        cvs = ProfileService.list_master_cvs(db, user_id)
        texts = [t.strip() for t in (master_cv_plain_text(c) for c in cvs) if t and t.strip()]
        if not texts:
            raise ValueError("用户没有可用的已上传简历，无法抽取")
        combined = "\n\n---\n\n".join(texts)

        extracted = extract_experience_library_from_text(llm, combined)

        facts = ExperienceLibraryService.upsert_candidate_facts(
            db,
            user_id,
            atoms=extracted.get("facts_atoms") or [],
            total_years_experience=extracted.get("total_years_experience"),
            source="extracted_from_master_cv",
            mark_confirmed=False,
        )

        created = 0
        for i, u in enumerate(extracted.get("experience_units") or []):
            unit = UserExperienceUnit(
                user_id=user_id,
                title=u.get("title"),
                employer=u.get("employer"),
                background=u.get("background"),
                actions=u.get("actions"),
                technologies=u.get("technologies") or [],
                ownership=u.get("ownership"),
                results=u.get("results"),
                domain=u.get("domain"),
                start_date=_to_date(u.get("start_year"), u.get("start_month")),
                end_date=_to_date(u.get("end_year"), u.get("end_month")),
                raw_date_text=u.get("raw_date_text"),
                raw_text=u.get("raw_text"),
                order_index=i,
                source="extracted_from_master_cv",
                confirmed=False,
            )
            db.add(unit)
            created += 1
        db.flush()

        return {"facts_atoms": len(facts.atoms or []), "experience_units": created}
