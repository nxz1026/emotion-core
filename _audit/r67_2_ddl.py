#!/usr/bin/env python3
"""R67-2 一次性 DDL：给 ladder_day 加 is_suspended 列（幂等）。

背景：ladder_day 是「按日 DELETE + INSERT」的派生表，新增列后 DDL 不会自动
生效（create_all 在本仓 src 内无调用方），必须手工 ALTER。

用法：PYTHONPATH=src .venv/bin/python _audit/r67_2_ddl.py
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from emotion_core.utils.db import connect, query_df  # noqa: E402

DDL = "ALTER TABLE ladder_day ADD COLUMN IF NOT EXISTS is_suspended boolean NOT NULL DEFAULT false"


def main() -> None:
    with connect() as conn:
        conn.execute(DDL)
        conn.commit()
    cols = query_df(
        "SELECT column_name, data_type, is_nullable, column_default"
        " FROM information_schema.columns WHERE table_name='ladder_day'"
        " ORDER BY ordinal_position"
    )
    print(cols.to_string())
    n = query_df("SELECT count(*) n FROM ladder_day WHERE is_suspended").iloc[0, 0]
    print(f"is_suspended=true 行数：{n}（期望 0，尚未重算）")


if __name__ == "__main__":
    main()
