"""
一次性快照脚本：把 user_job_scores 表全量导出成 JSON 文件，供切换打分模型（GLM/Qwen/DeepSeek
对比测试）前留档。DB 里的原始数据不受影响——UserJobScore 唯一约束是
(user_id, job_id, llm_model)，换模型重新打分只会新增行，不会覆盖旧的 gemini 记录；这份导出
是给「万一后续要清库/改表结构」留的额外保险，不是防覆盖用的。

跑法：python3 scripts/backup_job_scores.py [--llm-model gemini-3.1-flash-lite]
不带 --llm-model 时导出全表；带上则只导出匹配该模型名的行。
输出：data/backups/user_job_scores_<llm_model或all>_<timestamp>.json
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from db.session import SessionLocal
from db.models import UserJobScore

_BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"


def _row_to_dict(row: UserJobScore) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "job_id": row.job_id,
        "master_cv_id": row.master_cv_id,
        "recommended_cv_id": row.recommended_cv_id,
        "score": row.score,
        "decision": row.decision,
        "reason_summary": row.reason_summary,
        "requirement_matches": row.requirement_matches,
        "hard_constraints_hit": row.hard_constraints_hit,
        "score_breakdown": row.score_breakdown,
        "llm_model": row.llm_model,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm-model", default=None, help="只导出该模型名的记录；不传则导出全表")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = select(UserJobScore)
        if args.llm_model:
            query = query.where(UserJobScore.llm_model == args.llm_model)
        rows = db.execute(query).scalars().all()
    finally:
        db.close()

    _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    tag = args.llm_model or "all"
    out_path = _BACKUP_DIR / f"user_job_scores_{tag}_{ts}.json"
    out_path.write_text(
        json.dumps([_row_to_dict(r) for r in rows], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"导出 {len(rows)} 条记录 -> {out_path}")


if __name__ == "__main__":
    main()
