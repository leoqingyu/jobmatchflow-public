"""Web 前端只读/写入逻辑（供 FastAPI 路由调用）。"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.constants import (
    ASSETS_DEFERRED_TO_TRACKING_STATUSES,
    ApplicationStatus,
    AssetType,
    ScoringDecision,
)
from core.job_markets import JOB_MARKET_OPTIONS_ZH_EN
from db.models import (
    ApplicationTracking,
    GeneratedAsset,
    Job,
    PipelineRun,
    UserJobScore,
    UserMasterCV,
    UserProfile,
)
from services.cover_letter_generation_service import render_letter_preview, update_cover_letter_content
from services.resume_generation_service import is_tailored_resume_json, render_resume_preview, update_resume_content
from core.constants import MAX_USER_SAVED_RESUMES
from services.profile_service import ProfileService
from services.tracking_service import ApplicationTrackingService

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def public_settings_payload() -> dict[str, Any]:
    du = settings.database_url or ""
    preview = (du[:42] + "…") if len(du) > 42 else du
    return {
        "database_url_preview": preview,
        "gemini_model_name": settings.gemini_model_name,
        "score_threshold_review": settings.score_threshold_review,
        "score_threshold_generate": settings.score_threshold_generate,
        "storage_provider": settings.storage_provider,
        "notification_email_to": settings.notification_email_to or "",
        "job_markets": [
            {"label_zh": zh, "code": en} for zh, en in JOB_MARKET_OPTIONS_ZH_EN
        ],
        # 简历/求职信生成用哪个 LLM 的可选项——实际选中的值存在 UserProfile.generation_model，
        # 见 GET/PUT /users/{id}/generation-model。
        "generation_model_options": [
            {"id": "gemini", "label": "Gemini"},
            {"id": "claude", "label": "Claude"},
        ],
        # 简历 bullet 改写尺度的可选项——实际选中的值存在 UserProfile.resume_tailoring_mode，
        # 见 GET/PUT /users/{id}/resume-tailoring-mode，边界说明见 core.constants.ResumeTailoringMode。
        "resume_tailoring_mode_options": [
            {"id": "honest", "label": "Honest"},
            {"id": "jd_aligned", "label": "JD-aligned"},
        ],
        "pipeline_llm_model_name": settings.gemini_model_name,
        "claude_model_name": settings.claude_model_name,
    }


def dashboard_metrics(db: Session) -> dict[str, Any]:
    total_jobs = db.query(Job).count()
    total_scores = db.query(UserJobScore).count()
    tg = settings.score_threshold_generate
    high_score = db.query(UserJobScore).filter(UserJobScore.score >= tg).count()
    total_assets = db.query(GeneratedAsset).count()
    last_run = db.query(PipelineRun).order_by(PipelineRun.started_at.desc()).first()
    last_data = None
    if last_run:
        last_data = {
            "status": last_run.status,
            "started_at": last_run.started_at.isoformat() if last_run.started_at else None,
            "jobs_fetched": last_run.jobs_fetched,
            "jobs_scored": last_run.jobs_scored,
            "jobs_generated": last_run.jobs_generated,
            "jobs_notified": last_run.jobs_notified,
        }
    return {
        "total_jobs": total_jobs,
        "total_scores": total_scores,
        "high_score_count": high_score,
        "score_threshold_generate": tg,
        "total_assets": total_assets,
        "last_pipeline_run": last_data,
    }


def tracking_metrics(db: Session, user_id: int) -> dict[str, Any]:
    """过程指标优先：让早期用户看到有效动作，而不是只看 offer。全部基于当前状态快照。"""
    base = db.query(ApplicationTracking).filter(ApplicationTracking.user_id == user_id)
    applied = base.count()  # 有记录=已投递（见 ApplicationTrackingService.mark_applied：只在投递时创建）
    high_match = (
        db.query(UserJobScore).filter(
            UserJobScore.user_id == user_id,
            UserJobScore.score >= settings.score_threshold_generate,
        ).count()
    )
    interviews = base.filter(ApplicationTracking.application_status == ApplicationStatus.INTERVIEW.value).count()
    offers = base.filter(ApplicationTracking.application_status == ApplicationStatus.OFFER.value).count()
    week_count = base.filter(ApplicationTracking.applied_at >= datetime.now(timezone.utc) - timedelta(days=7)).count()
    return {"tracked": base.count(), "applied": applied, "high_match": high_match,
            "interviews": interviews, "offers": offers, "applied_last_7_days": week_count}


def funnel_metrics(db: Session, user_id: int) -> dict[str, Any]:
    """
    申请漏斗。三个口径不一样，别混：
    - matched：算法判定值得投（decision != discard）的岗位，**并集**上任何已经进入
      application_tracking 的岗位——用户手动投递一个算法本来 discard/没打过分的岗位
      （比如自己看中了直接投），也该算进"matched"，不然这个数会比 applied 还小，观感很怪。
      按 job_id 去重，不是简单相加。
    - applied：投递总数——application_tracking 里这个用户的全部记录数，不管当前推进到
      哪个阶段（interview/offer/rejected 都算），也不受任何时间窗限制。这是漏斗最上面
      一层"一共投了多少个"，不是"还停在 applied 阶段没推进"的子集——interviewed/offer/
      rejected 是这个总数往下的分支统计，不是从 applied 里减掉的。
    - interviewed：只要 status_history 里出现过 interview 这个阶段就算，不看**当前**状态——
      因为 OFFER/REJECTED 现在允许互改、且 REJECTED 既可能是"面试后被拒"也可能是"投完直接
      被拒"（见 core.constants.ApplicationStatus），一条记录推进到 offer/rejected 之后不该
      从"面试过"这个里程碑桶里消失，这是一个"曾经到达过"的累计统计，不是当前状态分桶。
    - offer/rejected：这两个是**当前**状态（且互斥——一条记录任何时刻只能是其中一个），
      互改后会此消彼长，不是各自独立递增的累计桶。
    """
    matched_score_job_ids = {
        jid
        for (jid,) in db.query(UserJobScore.job_id)
        .filter(
            UserJobScore.user_id == user_id,
            UserJobScore.decision != ScoringDecision.DISCARD.value,
        )
        .all()
    }
    base = db.query(ApplicationTracking).filter(ApplicationTracking.user_id == user_id)
    tracked_records = base.all()
    matched_job_ids = matched_score_job_ids | {r.job_id for r in tracked_records}

    applied = len(tracked_records)
    interviewed = sum(
        1
        for r in tracked_records
        if any((h or {}).get("status") == ApplicationStatus.INTERVIEW.value for h in (r.status_history or []))
    )
    offer = sum(1 for r in tracked_records if r.application_status == ApplicationStatus.OFFER.value)
    rejected = sum(1 for r in tracked_records if r.application_status == ApplicationStatus.REJECTED.value)
    return {
        "matched": len(matched_job_ids),
        "applied": applied,
        "interviewed": interviewed,
        "offer": offer,
        "rejected": rejected,
    }


def advance_tracking_status(db: Session, user_id: int, job_id: int, status: str) -> str:
    try:
        st = ApplicationStatus(status.strip().lower())
    except ValueError:
        raise ValueError(f"无效状态: {status}，可选: interview, offer, rejected") from None
    record = ApplicationTrackingService(db).advance_status(user_id, job_id, st)
    return record.application_status


def delete_tracking_record(db: Session, user_id: int, job_id: int) -> None:
    """撤销投递：岗位回到 Jobs 列表里"未投递"状态，见 ApplicationTrackingService.delete_tracking。"""
    ApplicationTrackingService(db).delete_tracking(user_id, job_id)


def fetch_tracking_rows(db: Session, user_id: int) -> tuple[list[dict], dict[int, dict[str, dict]]]:
    rows = (
        db.query(ApplicationTracking, Job)
        .join(Job, ApplicationTracking.job_id == Job.id)
        .filter(ApplicationTracking.user_id == user_id)
        .order_by(ApplicationTracking.updated_at.desc())
        .all()
    )
    if not rows:
        return [], {}
    tracking_rows: list[dict] = []
    for tr, job in rows:
        mcv_id = tr.applied_resume_master_cv_id
        mcv_name = None
        if mcv_id:
            mc = db.get(UserMasterCV, mcv_id)
            mcv_name = mc.cv_name if mc else None
        resume_src: str | None = None
        if tr.applied_resume_asset_id is not None:
            resume_src = "tailored"
        elif mcv_id is not None:
            resume_src = "library"
        tracking_rows.append(
            {
                "tracking_id": tr.id,
                "job_id": tr.job_id,
                "application_status": tr.application_status,
                "updated_at": tr.updated_at.isoformat() if tr.updated_at else None,
                "applied_at": tr.applied_at.isoformat() if tr.applied_at else None,
                "title": job.title,
                "company": job.company,
                "description_clean": job.description_clean,
                "description_raw": job.description_raw,
                "url": job.url,
                "applied_materials": {
                    "resume_source": resume_src,
                    "master_cv_id": mcv_id,
                    "master_cv_name": mcv_name,
                    "resume_asset_id": tr.applied_resume_asset_id,
                    "cover_letter_asset_id": tr.applied_cover_letter_asset_id,
                    "resume_snapshot": copy.deepcopy(tr.applied_resume_snapshot),
                    "cover_letter_snapshot": copy.deepcopy(tr.applied_cover_letter_snapshot),
                    "resume_file_path": tr.applied_resume_file_path,
                    "cover_letter_file_path": tr.applied_cover_letter_file_path,
                },
                "jd_snapshot_text": tr.jd_snapshot_text,
                "score_snapshot": copy.deepcopy(tr.score_snapshot),
                "status_history": copy.deepcopy(tr.status_history or []),
            }
        )
    job_ids = [r["job_id"] for r in tracking_rows]
    assets = (
        db.query(GeneratedAsset)
        .filter(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.job_id.in_(job_ids),
        )
        .all()
    )
    by_job: dict[int, dict[str, dict]] = {}
    for a in assets:
        by_job.setdefault(a.job_id, {})[a.asset_type] = {
            "id": a.id,
            "file_path": a.file_path,
        }
    return tracking_rows, by_job


def load_generated_asset_map(db: Session, user_id: int, job_id: int) -> dict[str, dict]:
    assets = (
        db.query(GeneratedAsset)
        .filter(
            GeneratedAsset.user_id == user_id,
            GeneratedAsset.job_id == job_id,
        )
        .all()
    )
    out: dict[str, dict] = {}
    for a in assets:
        out[a.asset_type] = {
            "id": a.id,
            "content_text": a.content_text,
            "content_json": copy.deepcopy(a.content_json) if a.content_json else {},
            "file_path": a.file_path,
        }
    return out


def _tracking_asset_dict_from_id(
    db: Session, asset_id: int | None, user_id: int, job_id: int
) -> dict | None:
    if not asset_id:
        return None
    a = db.get(GeneratedAsset, asset_id)
    if not a or a.user_id != user_id or a.job_id != job_id:
        return None
    cj = a.content_json
    return {
        "id": a.id,
        "content_json": copy.deepcopy(cj) if cj else {},
        "content_text": a.content_text,
        "file_path": a.file_path,
    }


_RESUME_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": _DOCX_MIME,
}


def _resume_file_response(fp: Path, filename_stem: str) -> tuple[bytes, str, str]:
    """按快照文件的实际扩展名返回文件名/MIME——库简历大多是原始 PDF，不能硬编码成 .docx，
    否则下载下来的文件名是 .docx 但内容其实是 PDF，打不开。"""
    suffix = fp.suffix.lower() or ".bin"
    mime = _RESUME_MIME_BY_SUFFIX.get(suffix, "application/octet-stream")
    return fp.read_bytes(), f"{filename_stem}{suffix}", mime


def tracking_download(
    db: Session, user_id: int, job_id: int, kind: str
) -> tuple[bytes, str, str]:
    tr = (
        db.query(ApplicationTracking)
        .filter(
            ApplicationTracking.user_id == user_id,
            ApplicationTracking.job_id == job_id,
        )
        .first()
    )
    if not tr:
        raise ValueError("No tracking record for this job")
    job = db.get(Job, job_id)
    if not job:
        raise ValueError("Job not found")
    amap = load_generated_asset_map(db, user_id, job_id)
    stem = "".join(
        c if c.isalnum() or c in "._-" else "_" for c in (job.title or "job")[:40]
    )
    k = kind.strip().lower()
    if k == "jd_txt":
        jd = tr.jd_snapshot_text or (job.description_clean or job.description_raw or "") or ""
        body = jd.strip() or "(No job description text)"
        return body.encode("utf-8"), f"jd_{job_id}_{stem}.txt", "text/plain; charset=utf-8"

    resume_a = amap.get(AssetType.RESUME_JSON.value)
    letter_a = amap.get(AssetType.MOTIVATION_LETTER.value)
    library_cid_override: int | None = None

    has_material_snapshot = (
        tr.applied_resume_asset_id is not None
        or tr.applied_resume_master_cv_id is not None
        or tr.applied_cover_letter_asset_id is not None
    )
    if has_material_snapshot:
        if tr.applied_resume_asset_id is not None:
            resume_a = _tracking_asset_dict_from_id(
                db, tr.applied_resume_asset_id, user_id, job_id
            )
        elif tr.applied_resume_master_cv_id is not None:
            resume_a = None
            library_cid_override = tr.applied_resume_master_cv_id
        if tr.applied_cover_letter_asset_id is not None:
            ld = _tracking_asset_dict_from_id(
                db, tr.applied_cover_letter_asset_id, user_id, job_id
            )
            if ld:
                letter_a = ld

    def _score_library_cid() -> int | None:
        score = (
            db.query(UserJobScore)
            .filter(
                UserJobScore.user_id == user_id,
                UserJobScore.job_id == job_id,
            )
            .first()
        )
        if not score:
            return None
        return score.recommended_cv_id or score.master_cv_id

    if k == "resume_docx":
        if tr.applied_resume_file_path and Path(tr.applied_resume_file_path).is_file():
            return _resume_file_response(Path(tr.applied_resume_file_path), f"resume_{job_id}_{stem}")
        if (
            has_material_snapshot
            and tr.applied_resume_asset_id is not None
            and not resume_a
        ):
            raise ValueError("The tailored resume snapshotted at apply time was deleted or is unavailable")
        if resume_a and resume_a.get("file_path"):
            fp = Path(resume_a["file_path"])
            if fp.is_file():
                return _resume_file_response(fp, f"resume_{job_id}_{stem}")
        cid = library_cid_override
        if cid is None:
            cid = _score_library_cid()
        if cid:
            cv_row = db.get(UserMasterCV, cid)
            if cv_row and cv_row.source_file_path:
                fp2 = Path(cv_row.source_file_path)
                if fp2.is_file():
                    return _resume_file_response(fp2, f"resume_{job_id}_{stem}")
        raise ValueError("No resume available to download yet — generate a tailored resume for this job, or upload a resume file in Settings")
    if k == "letter_docx":
        if tr.applied_cover_letter_file_path and Path(tr.applied_cover_letter_file_path).is_file():
            return Path(tr.applied_cover_letter_file_path).read_bytes(), f"cover_{job_id}_{stem}.docx", _DOCX_MIME
        if (
            has_material_snapshot
            and tr.applied_cover_letter_asset_id is not None
            and not letter_a
        ):
            raise ValueError("The cover letter snapshotted at apply time was deleted or is unavailable")
        if not letter_a or not letter_a.get("file_path"):
            raise ValueError("No cover letter available to download yet — generate one for this job first")
        fp = Path(letter_a["file_path"])
        if not fp.is_file():
            raise ValueError("Cover letter file is missing — please regenerate it")
        return fp.read_bytes(), f"cover_{job_id}_{stem}.docx", _DOCX_MIME
    raise ValueError(f"Unknown kind: {kind} (expected jd_txt, resume_docx, or letter_docx)")


def job_ids_resume_letter_deferred_to_tracking(db: Session, user_id: int) -> set[int]:
    rows = (
        db.query(ApplicationTracking.job_id)
        .filter(
            ApplicationTracking.user_id == user_id,
            ApplicationTracking.application_status.in_(ASSETS_DEFERRED_TO_TRACKING_STATUSES),
        )
        .all()
    )
    return {int(r[0]) for r in rows}


def ensure_resume_or_letter_asset_in_assets_preview(
    db: Session, user_id: int, asset: GeneratedAsset
) -> None:
    """已投递及后续跟进的岗位：简历/求职信仅从 Tracking 访问。"""
    if asset.asset_type not in (
        AssetType.RESUME_JSON.value,
        AssetType.MOTIVATION_LETTER.value,
    ):
        return
    st = (
        db.query(ApplicationTracking.application_status)
        .filter(
            ApplicationTracking.user_id == user_id,
            ApplicationTracking.job_id == asset.job_id,
        )
        .scalar()
    )
    if st and st in ASSETS_DEFERRED_TO_TRACKING_STATUSES:
        raise ValueError("该岗位已进入投递跟进，简历与求职信请在 Tracking 查看与下载")


def snapshot_assets_list(db: Session, user_id: int) -> dict[str, Any]:
    deferred = job_ids_resume_letter_deferred_to_tracking(db, user_id)
    rows = (
        db.query(GeneratedAsset, Job.title, Job.company)
        .join(Job, GeneratedAsset.job_id == Job.id)
        .filter(GeneratedAsset.user_id == user_id)
        .order_by(GeneratedAsset.created_at.desc())
        .all()
    )
    out: list[dict[str, Any]] = []
    excluded = 0
    for a, title, company in rows:
        if a.job_id in deferred and a.asset_type in (
            AssetType.RESUME_JSON.value,
            AssetType.MOTIVATION_LETTER.value,
        ):
            excluded += 1
            continue
        cj = a.content_json
        out.append(
            {
                "id": a.id,
                "user_id": a.user_id,
                "job_id": a.job_id,
                "asset_type": a.asset_type,
                "content_json": copy.deepcopy(cj) if cj else {},
                "content_text": a.content_text,
                "file_path": a.file_path,
                "job_title": title,
                "company": company,
                "is_tailored_resume": a.asset_type == AssetType.RESUME_JSON.value
                and is_tailored_resume_json(cj),
            }
        )
    return {
        "assets": out,
        "excluded_resume_letter_in_tracking_count": excluded,
    }


def preview_asset_html(db: Session, user_id: int, asset_id: int) -> str:
    """
    简历/求职信不再有 HTML 表示（DOCX 单模板，程序化拼装，没有 HTML 中间态）——这两类资产
    这里会返回空字符串，调用方（api_asset_preview_html）对空字符串统一 404。真正的预览
    留给前端重做时直接展示 content_json 或提供下载。
    """
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    ensure_resume_or_letter_asset_in_assets_preview(db, user_id, asset)
    return (asset.content_text or "").strip()


def save_asset_content_json(db: Session, user_id: int, asset_id: int, payload: dict) -> str:
    """编辑后直接重新渲染 DOCX 并覆盖旧文件，返回新文件路径。"""
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    ensure_resume_or_letter_asset_in_assets_preview(db, user_id, asset)
    if asset.asset_type == AssetType.RESUME_JSON.value:
        return update_resume_content(db, asset_id, user_id, payload)
    if asset.asset_type == AssetType.MOTIVATION_LETTER.value:
        return update_cover_letter_content(db, asset_id, user_id, payload)
    raise ValueError("仅支持 resume_json 或 motivation_letter")


def preview_asset_thumbnail(db: Session, user_id: int, asset_id: int, draft_payload: dict) -> dict:
    """Materials Editor 实时预览：按资产类型分发到简历还是求职信各自的预览渲染（见
    resume_generation_service.render_resume_preview / cover_letter_generation_service.
    render_letter_preview，两边都是"草稿合并、不落库、渲染 DOCX 转缩略图"同一个模式）。"""
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    if asset.asset_type == AssetType.MOTIVATION_LETTER.value:
        return render_letter_preview(db, asset_id, user_id, draft_payload)
    return render_resume_preview(db, asset_id, user_id, draft_payload)


def export_asset_file_bytes(db: Session, user_id: int, asset_id: int) -> tuple[bytes, str]:
    """
    返回 (文件内容, 文件名)。DOCX 在生成/编辑时已经同步渲染好，这里只是读取——不再有单独的
    "导出 PDF"这个按需渲染步骤（DOCX 生成很快，编辑时就已经落盘）。
    """
    asset = db.get(GeneratedAsset, asset_id)
    if not asset or asset.user_id != user_id:
        raise ValueError("资产不存在或无权访问")
    ensure_resume_or_letter_asset_in_assets_preview(db, user_id, asset)
    if asset.asset_type not in (AssetType.RESUME_JSON.value, AssetType.MOTIVATION_LETTER.value):
        raise ValueError("仅支持导出简历或求职信")
    if not asset.file_path:
        raise ValueError("尚未生成文件，请先生成一次")
    fp = Path(asset.file_path)
    if not fp.is_file():
        raise ValueError("文件不存在，请重新生成")
    return fp.read_bytes(), fp.name


def master_cv_payload(
    db: Session, user_id: int, *, include_full: bool = False
) -> dict[str, Any]:
    """兼容：返回 id 最小的一条简历摘要（旧单简历 UI）。"""
    row = ProfileService.get_only_master_cv_query(db, user_id).first()
    if not row:
        out: dict[str, Any] = {
            "id": None,
            "cv_name": None,
            "char_count": 0,
            "preview": "",
        }
        if include_full:
            out["full_text"] = ""
        return out
    md = row.cv_markdown or ""
    prev = md[:4000] if len(md) > 4000 else md
    out = {
        "id": row.id,
        "cv_name": row.cv_name,
        "char_count": len(md),
        "preview": prev,
    }
    if include_full:
        out["full_text"] = md
    return out


def master_cvs_bundle_payload(db: Session, user_id: int) -> dict[str, Any]:
    rows = ProfileService.list_master_cvs(db, user_id)
    prof = db.query(UserProfile).filter(UserProfile.user_id == user_id).first()
    lib = (prof.cv_material_library_html or "") if prof else ""
    lib_at = (
        prof.cv_material_library_updated_at.isoformat()
        if prof and prof.cv_material_library_updated_at
        else None
    )
    items = [
        {
            "id": r.id,
            "cv_name": r.cv_name,
            "char_count": len(r.cv_markdown or ""),
            "has_source_file": bool(
                r.source_file_path and Path(r.source_file_path).is_file()
            ),
            "is_pdf": bool(
                (r.source_file_path or "").lower().endswith(".pdf")
                and r.source_file_path
                and Path(r.source_file_path).is_file()
            ),
        }
        for r in rows
    ]
    return {
        "user_id": user_id,
        "max_slots": MAX_USER_SAVED_RESUMES,
        "items": items,
        "master_cv_html_char_count": len(lib),
        "master_cv_updated_at": lib_at,
        "material_library_char_count": len(lib),
        "material_library_updated_at": lib_at,
    }


def search_profile_countries(db: Session, user_id: int) -> list[str]:
    sp = ProfileService(db).get_default_search_profile(user_id)
    if not sp or not sp.countries:
        return []
    return list(sp.countries)
