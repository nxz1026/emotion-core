"""DB 连接层。psycopg3 直连 Postgres；主机与密码运行时取自 ~/.dbconfig。

分工（docs/13 §4.1、docs/11 §4）：
- data / orchestration：`connect()` —— 读写连接。
- presentation：`connect_ro()` —— 只读连接，连接串级 `default_transaction_read_only=on`，
  由服务端强制拒绝写操作（25006 ReadOnlySqlTransaction），从机制上保证展示层不写库（评审 P5；
  tests/architecture 扫描展示层只许 import `connect_ro`）。

~/.dbconfig 是字面量 `$KEY=value` 行，不是 shell 脚本：

    $RDSHOST=127.0.0.1
    $DB_PW=...

解析等价于 `sed -n 's/^\\$\\([A-Z_][A-Z0-9_]*\\)=\\(.*\\)$/\\1=\\2/p'`——**不 source、不起子进程**：
source 会执行文件内容（`$RDSHOST=x` 在 shell 里语法错误），子进程 sed 则多一层二进制依赖与静默失败面。
密码只在本模块内存与 libpq 握手之间出现，禁止进日志、异常消息、命令行参数。

纪律：
- 本模块是全项目唯一 psycopg 连接入口；DataFrame 由原生 cursor 装配，不引 SQLAlchemy。
- 策略阈值住 utils/config.py（config_hash 只覆盖策略值）；数据库口径住本文件，两者不混。
"""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg

logger = logging.getLogger(__name__)

# ── 非机密项（host / password 只在 ~/.dbconfig）──────────────
_DBCONFIG_FILE = "~/.dbconfig"
_HOST_KEY = "$RDSHOST"
_PASSWORD_KEY = "$DB_PW"
_PORT = 5432
_DBNAME = "longkonglong"  # 与 lkl/asel 同库（docs/evidence/merge-analysis §1.1）；换库只改这一行
_USER = "postgres"
_SSLMODE = "verify-full"
_SSLROOTCERT = "~/global-bundle.pem"

# 链路防挂死：握手 15s 上限 + TCP keepalive 探活（无超时的 connect 会永久睡）
_LIBPQ_TUNING: dict[str, Any] = {
    "connect_timeout": 15,
    "keepalives": 1,
    "keepalives_idle": 20,
    "keepalives_interval": 10,
    "keepalives_count": 3,
}

# sed -n 's/^\$\([A-Z_][A-Z0-9_]*\)=\(.*\)$/\1=\2/p' 的等价正则
_DBCONFIG_LINE_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

_secrets_cache: dict[str, str] | None = None
_secrets_mtime: float = 0.0


def _expand(path: str) -> str:
    return str(Path(path).expanduser())


def _read_secrets() -> dict[str, str]:
    """解析 ~/.dbconfig → {'$KEY': value}（进程内缓存，文件 mtime 失效）。"""
    global _secrets_cache, _secrets_mtime
    path = Path(_expand(_DBCONFIG_FILE))
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        raise RuntimeError(f"读不到 {_DBCONFIG_FILE}: {exc}") from exc
    if _secrets_cache is None or mtime > _secrets_mtime:
        out: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _DBCONFIG_LINE_RE.match(line.strip())
            if match is not None:
                out[f"${match.group(1)}"] = match.group(2).strip()
        _secrets_cache = out
        _secrets_mtime = mtime
    return _secrets_cache


def read_dbconfig() -> dict[str, Any]:
    """连接参数：host/password 取自 ~/.dbconfig，其余为模块常量。

    Returns:
        {host, port, dbname, user, password, sslmode, sslrootcert}；sslrootcert 已展开 ~。
    Raises:
        RuntimeError: ~/.dbconfig 不可读，或缺 $RDSHOST / $DB_PW。
    """
    secrets = _read_secrets()
    host = secrets.get(_HOST_KEY, "")
    password = secrets.get(_PASSWORD_KEY, "")
    if not host or not password:
        raise RuntimeError(f"{_DBCONFIG_FILE} 缺少 {_HOST_KEY} / {_PASSWORD_KEY}")
    return {
        "host": host,
        "port": _PORT,
        "dbname": _DBNAME,
        "user": _USER,
        "password": password,
        "sslmode": _SSLMODE,
        "sslrootcert": _expand(_SSLROOTCERT),
    }


def _kwargs(read_only: bool = False) -> dict[str, Any]:
    """libpq 连接参数；read_only=True 用 options 注入 default_transaction_read_only=on。"""
    kwargs: dict[str, Any] = {**read_dbconfig(), **_LIBPQ_TUNING}
    if read_only:
        kwargs["options"] = "-c default_transaction_read_only=on"
    return kwargs


def connect() -> psycopg.Connection[Any]:
    """读写连接（data / orchestration）。调用方负责 commit / rollback / close。"""
    conn = psycopg.connect(**_kwargs())
    logger.debug("connect rw %s:%s/%s", _DBNAME, _PORT, _USER)
    return conn


def connect_ro() -> psycopg.Connection[Any]:
    """只读连接（presentation 专用）。写操作被服务端拒绝，见模块文档。"""
    conn = psycopg.connect(**_kwargs(read_only=True))
    logger.debug("connect ro %s:%s/%s", _DBNAME, _PORT, _USER)
    return conn


def _fetch_df(conn: psycopg.Connection[Any], sql: str,
              params: Sequence[Any] | Mapping[str, Any]) -> pd.DataFrame:
    cur = conn.execute(sql, params)
    cols = [desc.name for desc in cur.description or ()]
    return pd.DataFrame(cur.fetchall(), columns=cols)


def query_df(sql: str, params: Sequence[Any] | Mapping[str, Any] = (),
             conn: psycopg.Connection[Any] | None = None) -> pd.DataFrame:
    """查询返回 DataFrame（原生 cursor，不依赖 SQLAlchemy）。

    传 conn 则复用调用方连接/事务（多次查询共享同一快照），否则自开自关。
    """
    if conn is not None:
        return _fetch_df(conn, sql, params)
    with psycopg.connect(**_kwargs()) as auto:
        return _fetch_df(auto, sql, params)


def execute(sql: str, params: Sequence[Any] | Mapping[str, Any] = (),
            conn: psycopg.Connection[Any] | None = None) -> int:
    """执行单条语句，返回受影响行数。

    传 conn 则不提交（由调用方控制事务）；否则自开事务，正常退出提交、异常回滚。
    """
    if conn is not None:
        return conn.execute(sql, params).rowcount
    with psycopg.connect(**_kwargs()) as auto:
        return auto.execute(sql, params).rowcount


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    self_test_log = logging.getLogger(f"{__name__}.__main__")

    with connect() as rw:
        self_test_log.info("connect()        select 1 -> %s", rw.execute("select 1").fetchone())
    with connect_ro() as ro:
        self_test_log.info(
            "connect_ro()     select 1 -> %s, default_transaction_read_only=%s",
            ro.execute("select 1").fetchone(),
            ro.execute("show default_transaction_read_only").fetchone()[0],
        )
