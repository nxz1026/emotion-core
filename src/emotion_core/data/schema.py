"""建表 DDL（幂等）。emotion-core 数据层 schema。

23 张表，按新项目需求设计（基于 lkl schema，去掉迁移逻辑）。
新库直接建最新 schema，不需要 _MIGRATIONS。

表清单：
- 行情：daily_bar, stock_basic
- 判据：derived_bar, limit_pool_em
- 市场：market_stat, ladder_day, promotion_day
- 信号：signal, signal_outcome, strategy_signal
- 交易：position, trade_event
- 报告：review_report, eval_result
- 题材：theme_tag, theme_group
- 运维：alert, llm_call_log, ingest_progress, hot_rank, pipeline_state, data_revision, watchlist
"""
from __future__ import annotations

DDL: dict[str, str] = {
    "stock_basic": """
        CREATE TABLE IF NOT EXISTS stock_basic (
            code         text PRIMARY KEY,
            name         text NOT NULL,
            list_date    date,
            market       text,
            is_st        boolean NOT NULL DEFAULT false,
            industry     text,
            market_cap   double precision,
            in_market    boolean NOT NULL DEFAULT true,
            float_shares double precision,
            first_bar_date date,
            updated_at   timestamptz NOT NULL DEFAULT now()
        )""",
    "daily_bar": """
        CREATE TABLE IF NOT EXISTS daily_bar (
            code          text NOT NULL,
            date          date NOT NULL,
            open          numeric(12,3),
            high          numeric(12,3),
            low           numeric(12,3),
            close         numeric(12,3),
            pre_close     numeric(12,3),
            volume        double precision,
            amount        double precision,
            turnover_rate numeric(8,4),
            PRIMARY KEY (code, date)
        )""",
    "derived_bar": """
        CREATE TABLE IF NOT EXISTS derived_bar (
            code          text NOT NULL,
            date          date NOT NULL,
            is_limit_up   boolean NOT NULL DEFAULT false,
            touched_limit boolean NOT NULL DEFAULT false,
            is_bomb       boolean NOT NULL DEFAULT false,
            is_one_word   boolean NOT NULL DEFAULT false,
            is_exchange   boolean NOT NULL DEFAULT false,
            is_limit_down boolean NOT NULL DEFAULT false,
            cont_days     integer NOT NULL DEFAULT 0,
            amplitude     numeric(8,4),
            PRIMARY KEY (code, date)
        )""",
    "limit_pool_em": """
        CREATE TABLE IF NOT EXISTS limit_pool_em (
            date          date NOT NULL,
            code          text NOT NULL,
            pool_type     text NOT NULL DEFAULT 'ZT',
            name          text,
            cont_days_em  integer,
            bomb_times    integer,
            first_seal    text,
            last_seal     text,
            turnover_rate numeric(8,4),
            PRIMARY KEY (date, code, pool_type)
        )""",
    "market_stat": """
        CREATE TABLE IF NOT EXISTS market_stat (
            date             date PRIMARY KEY,
            limit_up_count   integer,
            bomb_rate        numeric(6,4),
            zt_performance   numeric(8,4),
            max_limit_days   integer,
            limit_down_count integer,
            phase            text,
            buy_window       text,
            force_liquidate  boolean NOT NULL DEFAULT false,
            reason           text,
            top_amplitude    numeric(8,4),
            top_broke        boolean,
            bomb_threshold   numeric(6,4),
            has_candidate    boolean NOT NULL DEFAULT false,
            neg_feedback     integer NOT NULL DEFAULT 0,
            diverge          boolean NOT NULL DEFAULT false,
            zt_performance_mean       numeric(8,4),
            zt_performance_median numeric(8,4),
            tradable_max_days     integer,
            oneword_ratio         numeric(6,4),
            accelerate            boolean NOT NULL DEFAULT false,
            accel_reason          text,
            phase_inherited       boolean,
            dragon_env            text,
            dragon_env_reasons    jsonb,
            dragon_env_risks      jsonb
        )""",
    "promotion_day": """
        CREATE TABLE IF NOT EXISTS promotion_day (
            date             date NOT NULL,
            layer            text NOT NULL,
            promote_from     integer,
            promote_nominal  integer,
            promote_exchange integer,
            rate_nominal     numeric(6,4),
            rate_exchange    numeric(6,4),
            divergence       numeric(6,4),
            fail_perf        numeric(8,4),
            PRIMARY KEY (date, layer)
        )""",
    "ladder_day": """
        CREATE TABLE IF NOT EXISTS ladder_day (
            date               date NOT NULL,
            code               text NOT NULL,
            cont_days          integer NOT NULL,
            is_exchange        boolean NOT NULL DEFAULT false,
            is_top             boolean NOT NULL DEFAULT false,
            is_sole_top        boolean NOT NULL DEFAULT false,
            y_top_group_count  integer,
            y_top_survivor_count integer,
            PRIMARY KEY (date, code)
        )""",
    "signal": """
        CREATE TABLE IF NOT EXISTS signal (
            id             bigserial PRIMARY KEY,
            confirm_date   date NOT NULL,
            code           text NOT NULL,
            action         text NOT NULL,
            reason         text,
            buy_window     text,
            checklist      jsonb,
            status         text NOT NULL DEFAULT 'SUGGESTED',
            feedback_price numeric(12,3),
            feedback_date  date,
            strategy_version text,
            source         text,
            config_hash    text,
            created_at     timestamptz NOT NULL DEFAULT now(),
            UNIQUE (confirm_date, code, action)
        )""",
    "signal_outcome": """
        CREATE TABLE IF NOT EXISTS signal_outcome (
            confirm_date   date NOT NULL,
            code           text NOT NULL,
            action         text NOT NULL,
            entry_proxy    numeric(12,3),
            t1_gap         numeric(8,2),
            t1_promote     boolean,
            t1_close_ret   numeric(8,2),
            max_up5        numeric(8,2),
            max_dd5        numeric(8,2),
            t5_close_ret   numeric(8,2),
            rule_ret_a     numeric(8,2),
            rule_ret_d     numeric(8,2),
            complete       boolean NOT NULL DEFAULT false,
            updated_at     timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (confirm_date, code, action)
        )""",
    "hot_rank": """
        CREATE TABLE IF NOT EXISTS hot_rank (
            date  date NOT NULL,
            code  text NOT NULL,
            rank  integer,
            PRIMARY KEY (date, code)
        )""",
    "position": """
        CREATE TABLE IF NOT EXISTS position (
            id          bigserial PRIMARY KEY,
            code        text NOT NULL,
            entry_date  date NOT NULL,
            entry_price numeric(12,3) NOT NULL,
            shares      integer NOT NULL,
            status      text NOT NULL DEFAULT 'OPEN',
            note        text,
            closed_date date,
            close_price numeric(12,3)
        )""",
    "review_report": """
        CREATE TABLE IF NOT EXISTS review_report (
            date       date PRIMARY KEY,
            markdown   text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
    "eval_result": """
        CREATE TABLE IF NOT EXISTS eval_result (
            id         bigserial PRIMARY KEY,
            name       text NOT NULL,
            start_date date,
            end_date   date,
            params     jsonb,
            result     jsonb,
            config_hash text,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
    "llm_call_log": """
        CREATE TABLE IF NOT EXISTS llm_call_log (
            id                bigserial PRIMARY KEY,
            ts                timestamptz NOT NULL DEFAULT now(),
            profile           text,
            model             text,
            purpose           text,
            prompt_tokens     integer,
            completion_tokens integer,
            latency_ms        integer,
            cost_est          numeric(12,6),
            status            text
        )""",
    "ingest_progress": """
        CREATE TABLE IF NOT EXISTS ingest_progress (
            task    text NOT NULL,
            code    text NOT NULL,
            done_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (task, code)
        )""",
    "theme_tag": """
        CREATE TABLE IF NOT EXISTS theme_tag (
            date             date NOT NULL,
            code             text NOT NULL,
            primary_theme    text,
            secondary_themes text[],
            catalyst         text,
            catalyst_source  text,
            evidence_date    date,
            role             text,
            confidence       numeric(4,3),
            source           text NOT NULL DEFAULT 'wind',
            reviewed         boolean NOT NULL DEFAULT false,
            PRIMARY KEY (date, code)
        )""",
    "theme_group": """
        CREATE TABLE IF NOT EXISTS theme_group (
            date              date NOT NULL,
            theme             text NOT NULL,
            highest_board     integer,
            top_code          text,
            mid_count         integer,
            low_count         integer,
            first_board_count integer NOT NULL DEFAULT 0,
            completeness      numeric(5,1),
            status             text,
            member_count      integer,
            PRIMARY KEY (date, theme)
        )""",
    "strategy_signal": """
        CREATE TABLE IF NOT EXISTS strategy_signal (
            trade_date   date NOT NULL,
            code         text NOT NULL,
            strategy     text NOT NULL,
            prompt_hash  text NOT NULL,
            name         text,
            action       text NOT NULL CHECK (action IN ('BUY', 'WATCH', 'PASS')),
            score        integer NOT NULL CHECK (score BETWEEN 0 AND 100),
            confidence   numeric(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            reason       text NOT NULL,
            evidence     jsonb NOT NULL DEFAULT '{}'::jsonb,
            model        text,
            created_at   timestamptz NOT NULL DEFAULT now(),
            UNIQUE (trade_date, code, strategy, prompt_hash)
        )""",
    "pipeline_state": """
        CREATE TABLE IF NOT EXISTS pipeline_state (
            step text PRIMARY KEY,
            status text NOT NULL,
            for_date date,
            detail text,
            started_at timestamptz,
            finished_at timestamptz,
            exit_code integer,
            config_hash text
        )""",
    "trade_event": """
        CREATE TABLE IF NOT EXISTS trade_event (
            id bigserial PRIMARY KEY,
            user_name text,
            for_date date NOT NULL,
            action text NOT NULL,
            code text,
            status text NOT NULL,
            detail text,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
    "alert": """
        CREATE TABLE IF NOT EXISTS alert (
            id bigserial PRIMARY KEY,
            level text NOT NULL,
            source text NOT NULL,
            detail text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            acked_at timestamptz
        )""",
    "watchlist": """
        CREATE TABLE IF NOT EXISTS watchlist (
            code text PRIMARY KEY,
            name text,
            added_at date NOT NULL DEFAULT CURRENT_DATE,
            note text
        )""",
    "data_revision": """
        CREATE TABLE IF NOT EXISTS data_revision (
            id bigserial PRIMARY KEY,
            table_name text NOT NULL,
            trade_date date NOT NULL,
            detail text,
            created_at timestamptz NOT NULL DEFAULT now()
        )""",
    "trade_calendar": """
        CREATE TABLE IF NOT EXISTS trade_calendar (
            date date PRIMARY KEY,
            is_open boolean NOT NULL,
            source text NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now()
        )""",
}

_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS idx_daily_bar_date ON daily_bar (date)",
    "CREATE INDEX IF NOT EXISTS idx_derived_bar_date ON derived_bar (date)",
    "CREATE INDEX IF NOT EXISTS idx_derived_bar_limit ON derived_bar (date, is_limit_up)",
    "CREATE INDEX IF NOT EXISTS idx_ladder_day_date ON ladder_day (date)",
    "CREATE INDEX IF NOT EXISTS idx_signal_date ON signal (confirm_date)",
    "CREATE INDEX IF NOT EXISTS idx_theme_group_date ON theme_group (date)",
    "CREATE INDEX IF NOT EXISTS idx_strategy_signal_date ON strategy_signal (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_strategy_signal_strategy ON strategy_signal (strategy)",
    "CREATE UNIQUE INDEX IF NOT EXISTS position_open_code_uk"
    " ON position (code) WHERE status = 'OPEN'",
)


def create_all(conn) -> int:
    """在给定连接上建全部表+索引（幂等），返回表数。"""
    with conn.cursor() as cur:
        for stmt in [*DDL.values(), *_INDEXES]:
            cur.execute(stmt)
    return len(DDL)


def init_db(conn) -> int:
    """建表并回显清单。"""
    n = create_all(conn)
    tables = sorted(DDL)
    print(f"OK: {n} 张表就绪 -> {', '.join(tables)}")
    return n
