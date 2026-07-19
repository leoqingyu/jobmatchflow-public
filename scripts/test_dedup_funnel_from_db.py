#!/usr/bin/env python3
"""
从数据库读取已有岗位，按入库顺序模拟去重漏斗，
仅跑步骤 1（规范化整键）+ 步骤 2（同公司完整职位文本向量相似度），不调 LLM（灰区一律保留）。

与生产一致：`build_job_dedup_service(skip_llm=True)` + `dedupe_ordered_sequence`。

用法（在项目根目录）:
  python scripts/test_dedup_funnel_from_db.py
  python scripts/test_dedup_funnel_from_db.py --limit 500
  python scripts/test_dedup_funnel_from_db.py --show-removed 30

依赖：需安装 sentence-transformers（否则退化为仅步骤 1，脚本会提示）。
"""
from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy.orm import Session

from ai.job_dedup import (
    DedupRemovalExplain,
    build_job_dedup_service,
    funnel_step1_key,
    ordered_step1_removal_match,
)
from core.config import settings
from db.models import Job
from db.session import SessionLocal
from scraper.base import RawJobData


def _board_bucket(source: str | None) -> str:
    return (source or "other").strip().lower() or "other"


def _job_to_raw(row: Job) -> RawJobData:
    desc = row.description_clean or row.description_raw
    return RawJobData(
        source=row.source,
        external_job_id=row.external_job_id,
        title=row.title or "",
        company=row.company,
        location=row.location,
        country=row.country,
        url=row.url,
        description_raw=desc,
        date_posted=row.date_posted,
        extra={"db_id": row.id},
    )


def _load_ordered_jobs(db: Session, limit: int | None) -> list[RawJobData]:
    q = db.query(Job).order_by(Job.id.asc())
    if limit is not None and limit > 0:
        q = q.limit(limit)
    rows = q.all()
    return [_job_to_raw(r) for r in rows]


def _summarize_by_board(jobs: list[RawJobData], label: str) -> None:
    c = Counter(_board_bucket(j.source) for j in jobs)
    print(f"  [{label}] 按板块: {dict(c)} 合计={len(jobs)}")


def _db_id(j: RawJobData) -> str:
    if isinstance(j.extra, dict):
        v = j.extra.get("db_id")
        if v is not None:
            return str(v)
    return "—"


def _fmt_job_line(prefix: str, j: RawJobData) -> str:
    return (
        f"    {prefix} db_id={_db_id(j)} board={_board_bucket(j.source)!r} "
        f"source={j.source!r}\n"
        f"         公司={j.company!r} 职位={j.title!r}"
    )


def _run_ordered_dedup_with_pairs(
    ordered: list[RawJobData],
    svc,
) -> tuple[list[RawJobData], list[int], list[tuple[int, DedupRemovalExplain]]]:
    """
    与 ``dedupe_ordered_sequence`` / ``dedupe_ordered_deterministic`` 等价，
    并收集每条去掉记录对应的母记录（先出现者优先）。
    """
    kept: list[RawJobData] = []
    removed_idx: list[int] = []
    pairs: list[tuple[int, DedupRemovalExplain]] = []

    if svc is None:
        seen: set[str] = set()
        for i, job in enumerate(ordered):
            k = funnel_step1_key(job.company, job.title)
            if k in seen:
                removed_idx.append(i)
                mother = ordered_step1_removal_match(ordered, i)
                if mother is not None:
                    pairs.append((i, DedupRemovalExplain("step1_key_match", mother, None)))
            else:
                seen.add(k)
                kept.append(job)
        return kept, removed_idx, pairs

    for i, job in enumerate(ordered):
        ex = svc.explain_if_removed_vs_mother(kept, job)
        if ex is None:
            kept.append(job)
        else:
            removed_idx.append(i)
            pairs.append((i, ex))
    return kept, removed_idx, pairs


def main() -> None:
    parser = argparse.ArgumentParser(description="DB 岗位 → 漏斗去重前两步（无 LLM）")
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="最多读取条数（0=不限制，注意全表可能较慢）",
    )
    parser.add_argument(
        "--show-removed",
        type=int,
        default=15,
        help="打印前 N 条被去掉的记录（0=不打印样例）",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="不打印 job_dedup 的 INFO 日志（仍打印本脚本汇总）",
    )
    args = parser.parse_args()

    if not args.quiet:
        logging.basicConfig(
            level=logging.INFO,
            format="%(levelname)s %(name)s: %(message)s",
        )

    lim = args.limit if args.limit > 0 else None
    db = SessionLocal()
    try:
        ordered = _load_ordered_jobs(db, lim)
    finally:
        db.close()

    if not ordered:
        print("库中无岗位（或 limit 内为空）。")
        sys.exit(0)

    print("模拟顺序：按 job.id 升序（入库顺序）")
    print(f"配置: embed_model={settings.job_dedup_embed_model!r} "
          f"high={settings.job_dedup_embed_sim_high} low={settings.job_dedup_embed_sim_low}")
    _summarize_by_board(ordered, "输入")

    svc = build_job_dedup_service(skip_llm=True)
    if svc is None:
        print("\n⚠ 未加载向量服务（缺 sentence-transformers 或初始化失败），仅步骤 1（规范化整键）。")

    kept, removed_idx, removal_pairs = _run_ordered_dedup_with_pairs(ordered, svc)

    removed_rows = [ordered[i] for i in removed_idx]

    print(f"\n结果: 输入={len(ordered)} 保留={len(kept)} 去掉={len(removed_idx)}")
    _summarize_by_board(kept, "保留")
    _summarize_by_board(removed_rows, "去掉")

    if removal_pairs:
        print(
            "\n重复对（**对齐母记录** = 判重时对齐的那条；**去掉** = 当前条目）：\n"
            "  · 步骤1：同规范化整键时，母记录为母库中**按顺序第一条**同键岗位。\n"
            "  · 步骤2：为同规范化公司下、完整职位文本向量余弦**最大**的那条母记录（未必最早出现）。"
        )
        reason_cn = {
            "step1_key_match": "步骤1 规范化整键相同",
            "jd_fingerprint": "完整 JD 指纹相同",
            "embed_high": "步骤2 同公司完整职位文本向量相似度 > high",
            "llm_duplicate": "灰区 LLM 判重复",
        }
        for show_i, (rm_idx, ex) in enumerate(removal_pairs):
            if args.show_removed > 0 and show_i >= args.show_removed:
                break
            removed_job = ordered[rm_idx]
            reason = reason_cn.get(ex.reason, ex.reason)
            sim_s = f" sim={ex.similarity:.4f}" if ex.similarity is not None else ""
            print(f"  [{show_i + 1}] 原因: {reason}{sim_s}")
            print(_fmt_job_line("对齐母记录", ex.matched))
            print(_fmt_job_line("去掉", removed_job))

    if args.show_removed > 0 and removed_rows and not removal_pairs:
        n = min(args.show_removed, len(removed_rows))
        print(f"\n去掉样例（前 {n} 条）：")
        for j in removed_rows[:n]:
            eid = j.extra.get("db_id") if isinstance(j.extra, dict) else None
            print(
                f"  db_id={eid} board={_board_bucket(j.source)!r} source={j.source!r} "
                f"| {j.company!r} / {j.title!r}"
            )

    rb = Counter(_board_bucket(j.source) for j in removed_rows)
    if rb:
        print(f"\n去掉记录的来源分布: {dict(rb)}")

    sys.exit(0)


if __name__ == "__main__":
    main()
