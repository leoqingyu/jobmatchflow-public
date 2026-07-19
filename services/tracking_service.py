from datetime import datetime, timezone
from pathlib import Path
import shutil

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from core.logger import get_logger
from core.constants import ApplicationStatus, AssetType
from db.models import ApplicationTracking, GeneratedAsset, UserJobScore, Job, UserMasterCV
from services.profile_service import ProfileService
from services.resume_generation_service import is_tailored_resume_json
from services.resume_storage import remove_file_if_exists

logger = get_logger(__name__)

# APPLIED/INTERVIEW 单向不可回溯：只能往前推进，不能退回更早的阶段。OFFER/REJECTED 是
# 同一层级的两个终局结果，彼此之间允许互改（点错/事后变化，如 offer 被撤回、或误标 reject）——
# 这不算"回溯"，因为两者都不比对方更早，见 core.constants.ApplicationStatus 的说明。
#
# REJECTED 的合法下一步是路径相关的，不是固定的——纠正一条 rejected 记录时，"回到哪个
# 结果"取决于它当初是怎么走到 rejected 的：
# - 直接从 applied 拒的（没经过面试）：只能纠正成 interview（说明其实进了面试，是误标/
#   后续有变化），不能纠正成 offer——没面试过就直接给 offer 不合理。
# - 面试之后拒的：只能纠正成 offer（对应 interview 阶段本来的下一步），不能纠正成
#   interview——已经过了面试阶段，不该退回去。
# OFFER 只能来自 interview，所以它的下一步没有这种路径歧义，永远只是 {rejected}。
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    ApplicationStatus.APPLIED.value: {ApplicationStatus.INTERVIEW.value, ApplicationStatus.REJECTED.value},
    ApplicationStatus.INTERVIEW.value: {ApplicationStatus.OFFER.value, ApplicationStatus.REJECTED.value},
    ApplicationStatus.OFFER.value: {ApplicationStatus.REJECTED.value},
}


def _went_through_interview(record: ApplicationTracking) -> bool:
    return any(
        (h or {}).get("status") == ApplicationStatus.INTERVIEW.value
        for h in (record.status_history or [])
    )


def _allowed_next_statuses(record: ApplicationTracking) -> set[str]:
    current = record.application_status
    if current == ApplicationStatus.REJECTED.value:
        return (
            {ApplicationStatus.OFFER.value}
            if _went_through_interview(record)
            else {ApplicationStatus.INTERVIEW.value}
        )
    return _ALLOWED_TRANSITIONS.get(current, set())


class ApplicationTrackingService:
    def __init__(self, db: Session):
        self.db = db

    def _resolve_resume_choice(
        self, user_id: int, job_id: int, resume_choice: str
    ) -> tuple[GeneratedAsset | None, UserMasterCV | None]:
        """
        按 mark-applied 时用户的显式选择解析简历来源（生成的简历 xor 简历库槽位1/2）。
        不再像旧版那样自动"有定制简历就优先用"——用户点了哪个就用哪个。
        """
        if resume_choice == "tailored":
            resume_rows = (
                self.db.query(GeneratedAsset)
                .filter(
                    GeneratedAsset.user_id == user_id,
                    GeneratedAsset.job_id == job_id,
                    GeneratedAsset.asset_type == AssetType.RESUME_JSON.value,
                )
                .order_by(GeneratedAsset.id.desc())
                .all()
            )
            tailored = next(
                (a for a in resume_rows if is_tailored_resume_json(a.content_json)), None
            )
            if not tailored:
                raise ValueError('No tailored resume found for this job — can\'t select "Generated resume"')
            return tailored, None

        rows = ProfileService.list_master_cvs(self.db, user_id)
        idx = 0 if resume_choice == "slot_1" else 1
        if idx >= len(rows):
            raise ValueError(f"Resume {idx + 1} hasn't been uploaded yet")
        return None, rows[idx]

    def _fill_applied_material_snapshot(
        self, record: ApplicationTracking, user_id: int, job_id: int, resume_choice: str
    ) -> None:
        """写入投递时使用的材料内容、文件和 JD/评分证据快照。"""
        score = (
            self.db.query(UserJobScore)
            .filter(
                UserJobScore.user_id == user_id,
                UserJobScore.job_id == job_id,
            )
            .first()
        )
        job = self.db.get(Job, job_id)
        if not job:
            raise ValueError("Job not found")

        tailored, cv = self._resolve_resume_choice(user_id, job_id, resume_choice)

        letter = (
            self.db.query(GeneratedAsset)
            .filter(
                GeneratedAsset.user_id == user_id,
                GeneratedAsset.job_id == job_id,
                GeneratedAsset.asset_type == AssetType.MOTIVATION_LETTER.value,
            )
            .order_by(GeneratedAsset.id.desc())
            .first()
        )

        record.applied_resume_asset_id = tailored.id if tailored else None
        record.applied_resume_master_cv_id = cv.id if cv else None

        record.applied_cover_letter_asset_id = letter.id if letter else None
        record.applied_resume_snapshot = (
            dict(tailored.content_json or {}) if tailored else None
        )
        record.applied_cover_letter_snapshot = (
            dict(letter.content_json or {}) if letter else None
        )
        record.jd_snapshot_text = job.description_clean or job.description_raw or ""
        record.score_snapshot = {
            "score": score.score if score else None,
            "decision": score.decision if score else None,
            "reason_summary": score.reason_summary if score else None,
            "requirement_matches": list(score.requirement_matches or []) if score else [],
        }
        # 复制文件，避免重新生成时复用同一文件路径导致历史下载内容改变。
        snapshot_dir = Path("data") / "application_snapshots" / f"u{user_id}"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        for asset, attr, label in (
            (tailored, "applied_resume_file_path", "resume"),
            (letter, "applied_cover_letter_file_path", "cover_letter"),
        ):
            source = Path(asset.file_path) if asset and asset.file_path else None
            if source and source.is_file():
                target = snapshot_dir / f"tracking_{record.id}_{label}{source.suffix}"
                shutil.copy2(source, target)
                setattr(record, attr, str(target))
        if cv:
            record.applied_resume_snapshot = {
                "cv_name": cv.cv_name,
                "cv_markdown": cv.cv_markdown,
                "cv_json": cv.cv_json,
            }
            if cv.source_file_path and Path(cv.source_file_path).is_file():
                target = snapshot_dir / f"tracking_{record.id}_resume{Path(cv.source_file_path).suffix}"
                shutil.copy2(cv.source_file_path, target)
                record.applied_resume_file_path = str(target)

    def _get(self, user_id: int, job_id: int) -> ApplicationTracking | None:
        return (
            self.db.query(ApplicationTracking)
            .filter(ApplicationTracking.user_id == user_id, ApplicationTracking.job_id == job_id)
            .first()
        )

    def mark_applied(
        self, user_id: int, job_id: int, resume_choice: str = None, notes: str = None
    ) -> ApplicationTracking:
        """
        投递记录只在这里第一次创建；applied_at/status_history/application_status 只在
        第一次创建时写入，不会被重复点击重置或倒退（比如已经推进到 interview 之后又点
        "Mark as applied"，不会把状态打回 applied）。但记录已存在时会重新跑一遍
        _fill_applied_material_snapshot 刷新材料快照——快照代表"最近一次点击投递时用的
        材料"：改了简历重新生成、再点一次投递，就是明确要刷新它，不是幂等空操作。

        resume_choice（"slot_1"/"slot_2"/"tailored"）由用户在点击时显式勾选，见
        _resolve_resume_choice；这里必填，路由层已经用 Pydantic Literal 校验过，这个
        None 检查只是保护 service 层单独被调用的情况。
        """
        if resume_choice is None:
            raise ValueError("resume_choice is required")
        record = self._get(user_id, job_id)
        if record:
            if notes is not None:
                record.notes = notes
            self._fill_applied_material_snapshot(record, user_id, job_id, resume_choice)
            self.db.flush()
            logger.info(f"刷新投递材料快照 user={user_id} job={job_id}")
            return record
        now = datetime.now(timezone.utc)
        record = ApplicationTracking(
            user_id=user_id,
            job_id=job_id,
            application_status=ApplicationStatus.APPLIED.value,
            applied_at=now,
            last_stage_at=now,
            status_history=[{"status": ApplicationStatus.APPLIED.value, "at": now.isoformat()}],
            notes=notes,
        )
        self.db.add(record)
        self.db.flush()
        self._fill_applied_material_snapshot(record, user_id, job_id, resume_choice)
        self.db.flush()
        logger.info(f"标记已投递 user={user_id} job={job_id}")
        return record

    def advance_status(self, user_id: int, job_id: int, status: ApplicationStatus, notes: str = None) -> ApplicationTracking:
        """
        推进到下一个跟进阶段：APPLIED/INTERVIEW 只能前进不能倒退；OFFER/REJECTED 互为
        对方的合法"下一步"，允许来回改（终局结果的事后修正，不是倒退到更早阶段）。REJECTED
        的合法下一步取决于它是怎么走到这一步的（见 _allowed_next_statuses）。记录必须已存在
        （先 mark_applied 才有跟进阶段可推进）；目标状态必须是当前状态的合法下一步，否则抛
        ValueError（路由层转 400），不允许跳过校验强行写入。
        """
        record = self._get(user_id, job_id)
        if not record:
            raise ValueError("该岗位尚未标记为已投递，无法更新跟进状态")
        current = record.application_status
        allowed = _allowed_next_statuses(record)
        if status.value not in allowed:
            raise ValueError(
                f"不允许从「{current}」变为「{status.value}」（不能回溯，只能推进到: "
                f"{', '.join(sorted(allowed)) or '无（已是终态）'}）"
            )
        record.application_status = status.value
        now = datetime.now(timezone.utc)
        record.last_stage_at = now
        history = list(record.status_history or [])
        history.append({"status": status.value, "at": now.isoformat()})
        record.status_history = history
        if notes is not None:
            record.notes = notes
        self.db.flush()
        logger.info(f"更新投递状态 user={user_id} job={job_id} status={status.value}")
        return record

    def list_by_user(self, user_id: int, status: str = None) -> list[ApplicationTracking]:
        query = self.db.query(ApplicationTracking).filter(ApplicationTracking.user_id == user_id)
        if status:
            query = query.filter(ApplicationTracking.application_status == status)
        return query.order_by(ApplicationTracking.updated_at.desc()).all()

    def delete_tracking(self, user_id: int, job_id: int) -> None:
        """
        撤销投递：删除这条跟踪记录，岗位回到 Jobs 列表里"未投递"的状态（Jobs 列表的变灰
        只看这张表还有没有该 job_id 的记录，见 services/jobs_list_data.py 的 in_application）。
        跟"回溯状态"（不允许）是两回事——这是撤销整条投递记录，不是把 offer/rejected 退回
        interview/applied。连带删掉投递快照复制出来的文件，不留孤儿文件。
        """
        record = self._get(user_id, job_id)
        if not record:
            return
        remove_file_if_exists(record.applied_resume_file_path)
        remove_file_if_exists(record.applied_cover_letter_file_path)
        self.db.delete(record)
        self.db.flush()
        logger.info(f"撤销投递记录 user={user_id} job={job_id}")
