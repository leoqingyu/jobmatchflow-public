"""兼容旧 import：素材库字段现存放 Master CV HTML（由多份简历并集合并 JSON+模板渲染）。"""

from __future__ import annotations

from sqlalchemy.orm import Session

from services.master_cv_union_service import regenerate_master_cv_union_from_resume_sources


def regenerate_material_library_html(db: Session, user_id: int) -> str:
    """与旧名兼容；实际逻辑为从简历库 PDF 生成 Master CV HTML。"""
    _j, html = regenerate_master_cv_union_from_resume_sources(db, user_id)
    return html
