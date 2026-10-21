"""`lab` schema 的 DDL 与建表入口。

主线 `data/schema.py` 的 `create_all()` **没有任何调用方**，靠人工执行；
本模块同理，但给一个显式 CLI：

    PYTHONPATH=src .venv/bin/python -m emotion_core.lab.schema --create

所有表都在 `lab` schema 内，与 `public` 无外键、无触发器、无共享序列。
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

import psycopg

from emotion_core.lab import db as labdb

log = logging.getLogger(__name__)

DDL: dict[str, str] = {
    "lab_variant": """
        CREATE TABLE IF NOT EXISTS lab.lab_variant (
            variant_id  text PRIMARY KEY,
            params      jsonb NOT NULL,
            status      text NOT NULL DEFAULT 'draft',
            parent      text,
            note        text,
            created_at  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT lab_variant_status_chk
                CHECK (status IN ('draft','active','retired','pending'))
        )""",
    "lab_signal": """
        CREATE TABLE IF NOT EXISTS lab.lab_signal (
            variant_id   text NOT NULL,
            confirm_date date NOT NULL,
            code         text NOT NULL,
            action       text NOT NULL,
            phase        text,
            buy_window   text,
            tier         text,
            checklist    jsonb,
            PRIMARY KEY (variant_id, confirm_date, code, action)
        )""",
    "lab_outcome": """
        CREATE TABLE IF NOT EXISTS lab.lab_outcome (
            variant_id   text NOT NULL,
            confirm_date date NOT NULL,
            code         text NOT NULL,
            entry_px     numeric(12,3),
            exit_px      numeric(12,3),
            ret_pct      numeric(8,3),
            filled       boolean,
            sealed       boolean,
            broke        boolean,
            rolled       boolean,
            updated_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (variant_id, confirm_date, code)
        )""",
    "lab_score": """
        CREATE TABLE IF NOT EXISTS lab.lab_score (
            variant_id   text NOT NULL,
            window_start date NOT NULL,
            window_end   date NOT NULL,
            n            integer,
            fill_rate    numeric(5,2),
            expectancy   numeric(8,3),
            median       numeric(8,3),
            win_rate     numeric(5,2),
            lcb          numeric(8,3),
            worst        numeric(8,3),
            sub_worst    numeric(8,3),
            trials       integer,
            detail       jsonb,
            updated_at   timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (variant_id, window_start, window_end)
        )""",
    "lab_promotion": """
        CREATE TABLE IF NOT EXISTS lab.lab_promotion (
            id           bigserial PRIMARY KEY,
            variant_id   text NOT NULL,
            proposal     text NOT NULL,
            actions      jsonb,
            evidence     jsonb,
            status       text NOT NULL DEFAULT 'pending',
            decided_by   text,
            decided_at   timestamptz,
            created_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT lab_promotion_status_chk
                CHECK (status IN ('pending','approved','rejected'))
        )""",
}

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_lab_signal_date ON lab.lab_signal (confirm_date)",
    "CREATE INDEX IF NOT EXISTS idx_lab_outcome_date ON lab.lab_outcome (confirm_date)",
    "CREATE INDEX IF NOT EXISTS idx_lab_score_variant ON lab.lab_score (variant_id, window_end)",
)


def create_all(conn: psycopg.Connection[Any] | None = None) -> int:
    """建 schema + 表 + 索引，幂等。返回执行语句条数。"""
    own = conn is None
    active = conn if conn is not None else labdb.connect_lab()
    try:
        stmts = [f"CREATE SCHEMA IF NOT EXISTS {labdb.SCHEMA}"]
        stmts += list(DDL.values())
        stmts += list(_INDEXES)
        for sql in stmts:
            labdb.execute(active, sql)
        active.commit()
        return len(stmts)
    finally:
        if own:
            active.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="lab schema 建表（幂等）")
    ap.add_argument("--create", action="store_true", help="执行建表")
    args = ap.parse_args(argv)
    if not args.create:
        ap.print_help()
        return 2
    n = create_all()
    print(f"lab schema 就绪：{n} 条语句")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
