#!/usr/bin/env python3
"""
清空数据库业务数据，便于本地/测试前归零；上线前也可用于得到「干净」库（慎用 all）。

仅支持 PostgreSQL（与项目默认 DATABASE_URL 一致）。

用法:
  python scripts/reset_database.py jobs --yes    # 只清职位与抓取/评分/初筛等衍生表，保留 users / CV / 画像
  python scripts/reset_database.py all --yes     # 清空全部业务表（含用户与简历）
  python scripts/reset_database.py all           # 交互确认（输入 DELETE ）

生产环境 (ENVIRONMENT=production) 下执行 all 须额外加 --i-am-sure-production
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import text

from core.config import settings
from db.session import engine


def _require_postgres() -> None:
    if engine.dialect.name != "postgresql":
        print(
            f"错误：此脚本仅支持 PostgreSQL，当前连接 dialect={engine.dialect.name!r}。",
            file=sys.stderr,
        )
        sys.exit(1)


def reset_jobs_only() -> None:
    """清空 jobs 及其外键子表，并清空抓取日志；保留 users / profiles / master_cvs 等。"""
    stmts = [
        "TRUNCATE TABLE jobs RESTART IDENTITY CASCADE",
        "TRUNCATE TABLE job_ingestion_logs RESTART IDENTITY",
    ]
    with engine.begin() as conn:
        for sql in stmts:
            conn.execute(text(sql))


def reset_all() -> None:
    """清空所有业务表（先 jobs  CASCADE，再 users CASCADE，最后 ingestion 日志）。"""
    stmts = [
        "TRUNCATE TABLE jobs RESTART IDENTITY CASCADE",
        "TRUNCATE TABLE users RESTART IDENTITY CASCADE",
        "TRUNCATE TABLE job_ingestion_logs RESTART IDENTITY",
    ]
    with engine.begin() as conn:
        for sql in stmts:
            conn.execute(text(sql))


def main() -> None:
    parser = argparse.ArgumentParser(description="清空 JobSniper 数据库业务数据（PostgreSQL）")
    parser.add_argument(
        "mode",
        choices=("jobs", "all"),
        help="jobs=只清职位与相关衍生数据；all=含用户、简历、画像等全部清空",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="跳过交互确认（适合脚本/CI）",
    )
    parser.add_argument(
        "--i-am-sure-production",
        action="store_true",
        help="在 ENVIRONMENT=production 且 mode=all 时必须带上，否则拒绝执行",
    )
    args = parser.parse_args()

    _require_postgres()

    env = (settings.environment or "").strip().lower()
    if env == "production" and args.mode == "all" and not args.i_am_sure_production:
        print(
            "拒绝：当前 ENVIRONMENT=production，清空 all 极易误删线上数据。\n"
            "若确为有意操作，请追加参数：--i-am-sure-production",
            file=sys.stderr,
        )
        sys.exit(2)

    if not args.yes:
        print(f"即将执行 mode={args.mode}，目标库: {settings.database_url!r}")
        if args.mode == "all":
            line = input('确认请输入大写 DELETE 后回车（取消请直接回车）: ').strip()
            if line != "DELETE":
                print("已取消。")
                sys.exit(0)
        else:
            line = input('确认请输入 YES 后回车（取消请直接回车）: ').strip()
            if line != "YES":
                print("已取消。")
                sys.exit(0)

    if args.mode == "jobs":
        reset_jobs_only()
        print("已清空：jobs（及 CASCADE 子表）与 job_ingestion_logs；users / CV / 画像已保留。")
    else:
        reset_all()
        print("已清空：jobs、users（及 CASCADE 子表）与 job_ingestion_logs。")


if __name__ == "__main__":
    main()
