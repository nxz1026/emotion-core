"""批量回填 ST PIT（``ref_security_status``）。

用 Wind ``search_stocks`` 按半年窗口枚举「实施ST / 撤销ST」事件，折成区间写入
``ref_security_status``，替代逐股烧配额的 ``get_stock_events`` 路径：
全量 2024 起约 10~20 次调用（窗口触到 100 行硬上限会自动二分）。

用法（仓库根）：

    PYTHONPATH=src DB_CONFIG=/home/ubuntu/.dbconfig \\
        .venv/bin/python scripts/backfill_st_pit_wind.py [起始日期 YYYY-MM-DD]

幂等：按 code 全量替换该股的 ST 区间。
"""
from __future__ import annotations

import logging
import sys
from datetime import date

from emotion_core.services.ref_security_status import backfill_security_status_bulk


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    start = date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date(2024, 1, 1)
    codes, rows = backfill_security_status_bulk(start=start)
    print(f"OK: {codes} 个 code，{rows} 条 ST 区间（起始 {start}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
