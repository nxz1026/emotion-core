//! 判据 + 连板。语义逐字照搬 lkl/services/derive.py（Python 参考实现
//! `emotion_core/algorithms/indicators.py`）。
//!
//! 关键口径（不得改动）：
//! - is_limit_up: close == limit_up_price(pre_close)（精确等于，不是 >=）
//! - is_one_word: low >= limit_up_price（一字板）
//! - is_exchange: is_limit_up AND low < limit_up_price（换手板）
//! - is_bomb: touched_limit AND NOT is_limit_up（炸板）
//! - touched_limit: high >= limit_up_price（曾触板）
//! - cont_days: 停牌断档不打断（只遍历实际存在的行，不按日历补齐）

// pyo3 0.20 的 `#[pymethods]` 宏在 rustc ≥1.80 会触发 non_local_definitions 误报，抑制之。
#![allow(non_local_definitions)]

use pyo3::prelude::*;
use std::collections::HashMap;

/// 涨跌幅千分比（主板 100 = 10.0%，创业/科创 200 = 20%，北交所 300 = 30%）。
fn board_pct_milli(code: &str) -> i64 {
    if code.starts_with("bj") {
        300 // 北交所 30%（akshare symbol 格式）
    } else if code.starts_with("30") || code.starts_with("68") {
        200 // 创业板/科创板 20%
    } else if code.starts_with('4') || code.starts_with('8') {
        300 // 北交所 30%（纯代码格式）
    } else {
        100 // 主板 10%
    }
}

/// 涨停价（分）。四舍五入与 SQL 整数式 (x*pct + 50)/100 逐分一致。
fn limit_up_price_cents(pre_close_cents: i64, code: &str) -> i64 {
    let pct = board_pct_milli(code);
    (pre_close_cents * (1000 + pct) + 500) / 1000
}

/// 跌停价（分）。
fn limit_down_price_cents(pre_close_cents: i64, code: &str) -> i64 {
    let pct = board_pct_milli(code);
    (pre_close_cents * (1000 - pct) + 500) / 1000
}

/// 是否涨停（收盘 == 涨停价，精确等于，不是 >=）。
fn is_limit_up(close_cents: i64, pre_close_cents: i64, code: &str) -> bool {
    close_cents == limit_up_price_cents(pre_close_cents, code)
}

/// 是否跌停（收盘 == 跌停价，精确等于，不是 <=）。
fn is_limit_down(close_cents: i64, pre_close_cents: i64, code: &str) -> bool {
    close_cents == limit_down_price_cents(pre_close_cents, code)
}

/// 原始日线。价格一律用分（i64），消灭浮点。
#[pyclass(name = "Bar")]
pub struct Bar {
    #[pyo3(get)]
    pub code: String,
    /// 交易日，ISO "YYYY-MM-DD"（datetime.date 的 str() 形式，字典序即时间序）。
    #[pyo3(get)]
    pub date: String,
    #[pyo3(get)]
    pub open_cents: i64,
    #[pyo3(get)]
    pub high_cents: i64,
    #[pyo3(get)]
    pub low_cents: i64,
    #[pyo3(get)]
    pub close_cents: i64,
    #[pyo3(get)]
    pub pre_close_cents: i64,
    /// 手（非股）。
    #[pyo3(get)]
    pub volume: i64,
    /// 百分数（5.23 表示 5.23%）。
    #[pyo3(get)]
    pub turnover_rate: f64,
}

#[pymethods]
impl Bar {
    /// 接受 datetime.date（str() → "YYYY-MM-DD"）或 ISO 字符串。
    #[new]
    fn new(
        code: String,
        date: &PyAny,
        open_cents: i64,
        high_cents: i64,
        low_cents: i64,
        close_cents: i64,
        pre_close_cents: i64,
        volume: i64,
        turnover_rate: f64,
    ) -> PyResult<Self> {
        let date: String = date.str()?.extract()?;
        Ok(Bar {
            code,
            date,
            open_cents,
            high_cents,
            low_cents,
            close_cents,
            pre_close_cents,
            volume,
            turnover_rate,
        })
    }
}

/// 让 `compute_derived(bars: Vec<Bar>)` 能按值提取 `Bar`（`#[pyclass]` 默认只对
/// `Py<T>` / `PyRef<T>` 实现 `FromPyObject`，不对 owned `T` 实现）。
impl<'py> FromPyObject<'py> for Bar {
    fn extract(ob: &'py PyAny) -> PyResult<Self> {
        let b: PyRef<'py, Bar> = ob.extract()?;
        Ok(Bar {
            code: b.code.clone(),
            date: b.date.clone(),
            open_cents: b.open_cents,
            high_cents: b.high_cents,
            low_cents: b.low_cents,
            close_cents: b.close_cents,
            pre_close_cents: b.pre_close_cents,
            volume: b.volume,
            turnover_rate: b.turnover_rate,
        })
    }
}

/// 判据输出（derived_bar 表）。
#[pyclass(name = "DerivedBar")]
pub struct DerivedBar {
    #[pyo3(get)]
    pub code: String,
    #[pyo3(get)]
    pub date: String,
    // 判据
    #[pyo3(get)]
    pub is_limit_up: bool,
    #[pyo3(get)]
    pub is_limit_down: bool,
    /// 一字板（low >= limit_up_price）
    #[pyo3(get)]
    pub is_one_word: bool,
    /// 换手板（is_limit_up AND low < limit_up_price）
    #[pyo3(get)]
    pub is_exchange: bool,
    /// 炸板（touched_limit AND NOT is_limit_up）
    #[pyo3(get)]
    pub is_bomb: bool,
    /// 曾触板（high >= limit_up_price）
    #[pyo3(get)]
    pub touched_limit: bool,
    // 连板
    /// 连板数（停牌断档不打断，C1）
    #[pyo3(get)]
    pub cont_days: i64,
    // 振幅
    #[pyo3(get)]
    pub amplitude: f64,
    // 质量
    #[pyo3(get)]
    pub quality: String,
}

/// 从原始日线计算判据 + 连板。
///
/// 停牌断档处理：只遍历实际存在的行，遇到非涨停行重置计数。
/// 停牌日无行，自然实现「停牌不断」（C1）。
#[pyfunction]
pub fn compute_derived(bars: Vec<Bar>) -> Vec<DerivedBar> {
    // 按 code 分组，用 `order` 保留首次出现顺序（等价 Python dict 插入序）。
    let mut order: Vec<String> = Vec::new();
    let mut by_code: HashMap<String, Vec<Bar>> = HashMap::new();
    for b in bars {
        if !by_code.contains_key(&b.code) {
            order.push(b.code.clone());
        }
        by_code.entry(b.code.clone()).or_default().push(b);
    }

    let mut result: Vec<DerivedBar> = Vec::new();
    for code in order {
        let mut rows = by_code.remove(&code).unwrap_or_default();
        // 保持日期升序（ISO 字符串字典序 == 时间序）
        rows.sort_by(|a, b| a.date.cmp(&b.date));
        let mut cont: i64 = 0;
        for b in rows {
            let lim_up = limit_up_price_cents(b.pre_close_cents, &code);
            let lu = is_limit_up(b.close_cents, b.pre_close_cents, &code);
            let ld = is_limit_down(b.close_cents, b.pre_close_cents, &code);
            let ow = b.low_cents >= lim_up; // 一字板
            let ex = lu && b.low_cents < lim_up; // 换手板
            let tl = b.high_cents >= lim_up; // 曾触板
            let bomb = tl && !lu; // 炸板
            if lu {
                cont += 1;
            } else {
                cont = 0;
            }
            // 振幅 = (high - low) / pre_close * 100，四舍五入到 4 位
            let amp = if b.pre_close_cents != 0 {
                (b.high_cents - b.low_cents) as f64 / b.pre_close_cents as f64 * 100.0
            } else {
                0.0
            };
            let amplitude = (amp * 10000.0).round() / 10000.0;
            result.push(DerivedBar {
                code: code.clone(),
                date: b.date,
                is_limit_up: lu,
                is_limit_down: ld,
                is_one_word: ow,
                is_exchange: ex,
                is_bomb: bomb,
                touched_limit: tl,
                cont_days: cont,
                amplitude,
                quality: "valid".to_string(),
            });
        }
    }
    result
}
