"""Lab 的数据库入口：只写 `lab` schema，`public` 只读。

为什么要有这层：主线与实验共用同一个数据库实例。真正的硬边界是「不同角色 + 只授权
`lab` 写权限」，但当前 `~/.dbconfig` 只有主账号口令，建独立角色需要再落一条口令到
该文件（属用户凭据文件，未获准不动）。在此之前用**语句级守卫**兜住：

- `execute()` 只放行目标为 `lab.` 的写语句，其余写语句直接抛 `LabWriteDenied`；
- 会话级 `search_path=lab,public`，未加 schema 前缀的写语句会落到 `lab`，不会误伤 `public`；
- 读一律走 `query_df()`，只对白名单表开放（防止顺手 join 到不该读的东西）。

升级到硬边界只需：`CREATE ROLE lab_runner` + 把 `$LAB_PW` 写进 `~/.dbconfig`，
然后把本模块的 `connect_lab()` 换成独立账号 —— 调用方不变。
"""

from __future__ import annotations

import logging
import re
from typing import Any

import pandas as pd
import psycopg

from emotion_core.utils import db as dbmod

log = logging.getLogger(__name__)

SCHEMA = "lab"

#: 允许读的 public 表（只读白名单）。新增需求必须先在这里登记。
READ_TABLES: frozenset[str] = frozenset(
    {
        "daily_bar",
        "derived_bar",
        "market_stat",
        "ladder_day",
        "limit_pool_em",
        "signal",
        "signal_outcome",
        "trade_calendar",
        "stock_basic",
        "promotion_day",
    }
)

_WRITE_HEAD = re.compile(
    r"^\s*(?:insert\s+into|update|delete\s+from|alter\s+table|drop\s+table|"
    r"create\s+(?:table|view|schema)|truncate(?:\s+table)?|grant|revoke)\s+"
    r"(?:if\s+not\s+exists\s+)?([\w\".]+)",
    re.IGNORECASE,
)


class LabWriteDenied(RuntimeError):
    """试图写 `lab` 之外的 schema。"""


def connect_lab() -> psycopg.Connection[Any]:
    """Lab 专用连接：复用主账号，但会话 search_path 限定在 lab 优先。"""
    conn = dbmod.connect()
    conn.execute(f"SET search_path TO {SCHEMA}, public")
    conn.commit()
    return conn


def check_write(sql: str) -> None:
    """写语句守卫：目标必须是 `lab.` 开头的表。"""
    m = _WRITE_HEAD.match(sql)
    if not m:
        return
    target = m.group(1).strip().strip('"').lower()
    if target == SCHEMA or target.startswith(f"{SCHEMA}."):
        return
    raise LabWriteDenied(f"lab 层禁止写 {target}：实验代码只允许写 {SCHEMA}.*（docs/20 §2）")


def execute(
    conn: psycopg.Connection[Any], sql: str, params: tuple[Any, ...] | list[Any] | None = None
) -> psycopg.Cursor[Any]:
    """唯一的写入口：先过守卫再执行。"""
    check_write(sql)
    return conn.execute(sql, params)


def query_df(sql: str, params: tuple[Any, ...] | list[Any] | None = None) -> pd.DataFrame:
    """只读查询。`public` 部分只允许白名单表（按 SQL 里出现的表名检查）。"""
    lowered = sql.lower()
    cte = set(re.findall(r"(?:\bwith|,)\s+([a-z_][a-z0-9_]*)\s+as\s*\(", lowered))
    for ref in re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_.]*)", lowered):
        if ref in {"select", "lateral"} or ref in cte:
            continue
        schema, _, name = ref.rpartition(".")
        if schema and schema != SCHEMA:
            raise LabWriteDenied(f"lab 层不允许读 {ref}：只读 public 白名单 + lab 自己的表")
        if name not in READ_TABLES and schema != SCHEMA:
            raise LabWriteDenied(f"lab 层不允许读 {name}：未在 READ_TABLES 登记")
    return dbmod.query_df(sql, params if params is not None else ())
