#!/usr/bin/env python3
"""
定时抓取调度器入口（阻塞进程，前台/nohup 常驻运行）。

用法（项目根目录）：
  conda activate jobsniper
  python scripts/run_scheduler.py
  # 或后台常驻：
  nohup python scripts/run_scheduler.py > scheduler.log 2>&1 &

Ctrl+C 或 kill 该进程即可停止；不做每日固定重启，长期运行请自行配置进程守护
（systemd / supervisor / docker 等），本脚本本身不含守护逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tasks.scheduler import start_scheduler

if __name__ == "__main__":
    start_scheduler()
