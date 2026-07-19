#!/usr/bin/env python3
"""
打分 + 生成长驻进程入口——**已不再例行运行**。DeepSeek 引入峰谷定价后，常驻轮询会不分时段
持续调用 DeepSeek，也无法保证同一用户连续处理（命中不了 DeepSeek 的上下文缓存），两个问题
都不可接受。例行打分现在拆成两条独立节奏，见 tasks/score_tasks.py：
  - Gemini JD 提取（job 级）走 Batch API，每 30 分钟提交/轮询一次。
  - DeepSeek 逐项匹配走北京时间非高峰批次（03:00 / 18:30），每个用户连续处理完再换下一个。
用户手动点击「开始匹配」（api/experience_routes.py::api_start_job_search）会立即同步处理。

这个脚本保留下来只是为了手动调试单条打分逻辑方便，不要再 nohup 常驻运行它。

用法（项目根目录，仅手动调试用）：
  conda activate jobsniper
  python scripts/run_matching_worker.py

Ctrl+C 或 kill 该进程即可停止。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from services.matching_worker_service import run_matching_worker_forever

if __name__ == "__main__":
    run_matching_worker_forever()
