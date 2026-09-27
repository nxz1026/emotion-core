//! L3 信号结果回填纯逻辑（outcome.py 的 Rust 移植）。
//!
//! 语义逐字照搬 src/emotion_core/algorithms/outcome.py：
//! - entry_proxy = 信号次日开盘价（round 3 位）；
//! - t1_gap / t1_close_ret / max_up5 / max_dd5 / t5_close_ret 均相对
//!   **未舍入** entry 计算（Python 原样：_pct 入参是 nb["open"].iloc[0]，
//!   不是 round 后的 entry_proxy）；
//! - 前向 <5 根 bar → max_up5 / max_dd5 / t5_close_ret 记 None、
//!   complete=False（右删失不近似，与 evaluate 口径一致）；
//! - 信号日无收盘价 / 前向无 bar → None（缺数不入库）；
//! - rule_ret_a / rule_ret_d 取自出口族 A_断板开盘 / D_半仓（dict 查找，
//!   缺键 → None）。
//!
//! SQL 取数（基准日收盘价、前向 bar、涨停止、出口族）留在 Python 侧，
//! Rust 只算纯行组装逻辑。

use pyo3::prelude::*;

/// Python round(x, n) 语义：十进制正确舍入 + round-half-even
/// （CPython float.__round__ 走 _Py_dg_dtoa 十进制路径，非二进制
/// half-away-from-zero）。
fn py_round(x: f64, ndigits: u32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits as usize, x).parse().unwrap_or(x)
}

/// 前瞻结果行（对应 _outcome_row 返回的 tuple，列序同 _COLS）。
#[pyclass]
#[derive(Clone)]
pub struct OutcomeRow {
    #[pyo3(get, set)]
    pub confirm_date: String,
    #[pyo3(get, set)]
    pub code: String,
    #[pyo3(get, set)]
    pub action: String,
    #[pyo3(get, set)]
    pub entry_proxy: f64,
    #[pyo3(get, set)]
    pub t1_gap: f64,
    #[pyo3(get, set)]
    pub t1_promote: bool,
    #[pyo3(get, set)]
    pub t1_close_ret: f64,
    #[pyo3(get, set)]
    pub max_up5: Option<f64>,
    #[pyo3(get, set)]
    pub max_dd5: Option<f64>,
    #[pyo3(get, set)]
    pub t5_close_ret: Option<f64>,
    #[pyo3(get, set)]
    pub rule_ret_a: Option<f64>,
    #[pyo3(get, set)]
    pub rule_ret_d: Option<f64>,
    #[pyo3(get, set)]
    pub complete: bool,
}

#[pymethods]
impl OutcomeRow {
    #[new]
    #[pyo3(signature = (confirm_date, code, action, entry_proxy, t1_gap, t1_promote, t1_close_ret, max_up5, max_dd5, t5_close_ret, rule_ret_a, rule_ret_d, complete))]
    pub fn new(
        confirm_date: String,
        code: String,
        action: String,
        entry_proxy: f64,
        t1_gap: f64,
        t1_promote: bool,
        t1_close_ret: f64,
        max_up5: Option<f64>,
        max_dd5: Option<f64>,
        t5_close_ret: Option<f64>,
        rule_ret_a: Option<f64>,
        rule_ret_d: Option<f64>,
        complete: bool,
    ) -> Self {
        OutcomeRow {
            confirm_date,
            code,
            action,
            entry_proxy,
            t1_gap,
            t1_promote,
            t1_close_ret,
            max_up5,
            max_dd5,
            t5_close_ret,
            rule_ret_a,
            rule_ret_d,
            complete,
        }
    }

    /// Python 侧 clone()（pyo3 不自动暴露 Clone trait）。
    fn clone(&self) -> OutcomeRow {
        OutcomeRow {
            confirm_date: self.confirm_date.clone(),
            code: self.code.clone(),
            action: self.action.clone(),
            entry_proxy: self.entry_proxy,
            t1_gap: self.t1_gap,
            t1_promote: self.t1_promote,
            t1_close_ret: self.t1_close_ret,
            max_up5: self.max_up5,
            max_dd5: self.max_dd5,
            t5_close_ret: self.t5_close_ret,
            rule_ret_a: self.rule_ret_a,
            rule_ret_d: self.rule_ret_d,
            complete: self.complete,
        }
    }
}

/// 百分比变化（对应 _pct）：round((new/base - 1) * 100, 2）。

#[pyfunction]
pub fn pct(new: f64, base: f64) -> f64 {
    py_round((new / base - 1.0) * 100.0, 2)
}

/// 单信号前瞻行（对应 _outcome_row 的纯逻辑部分）。
///
/// 入参为 Python 侧 SQL 取数结果：
/// - base：信号日收盘价；None = 缺数（返回 None）
/// - nb_open / nb_high / nb_low / nb_close：前向 bar（次日开盘起，升序）；
///   空 = 前向无 bar（返回 None）
/// - nb_limit_up：前向 bar 逐日是否涨停（t1_promote 取首日；空 → False，
///   同 Python _limit_up_on 查不到行的兜底）
/// - rule_ret_a / rule_ret_d：出口族 A_断板开盘 / D_半仓（缺键 → None）
#[pyfunction]
#[pyo3(signature = (confirm_date, code, action, base, nb_open, nb_high, nb_low, nb_close, nb_limit_up, rule_ret_a, rule_ret_d))]
pub fn outcome_row(
    confirm_date: String,
    code: String,
    action: String,
    base: Option<f64>,
    nb_open: Vec<f64>,
    nb_high: Vec<f64>,
    nb_low: Vec<f64>,
    nb_close: Vec<f64>,
    nb_limit_up: Vec<bool>,
    rule_ret_a: Option<f64>,
    rule_ret_d: Option<f64>,
) -> Option<OutcomeRow> {
    let base = base?;
    if nb_open.is_empty() {
        return None;
    }
    let entry = nb_open[0];
    let mut row = OutcomeRow {
        confirm_date,
        code,
        action,
        entry_proxy: py_round(entry, 3),
        t1_gap: pct(entry, base),
        t1_promote: nb_limit_up.first().copied().unwrap_or(false),
        t1_close_ret: pct(nb_close[0], entry),
        max_up5: None,
        max_dd5: None,
        t5_close_ret: None,
        rule_ret_a,
        rule_ret_d,
        complete: false,
    };
    if nb_open.len() >= 5 {
        let max_high = nb_high.iter().take(5).fold(f64::NEG_INFINITY, |a, &b| a.max(b));
        let min_low = nb_low.iter().take(5).fold(f64::INFINITY, |a, &b| a.min(b));
        row.max_up5 = Some(pct(max_high, entry));
        row.max_dd5 = Some(pct(min_low, entry));
        row.t5_close_ret = Some(pct(nb_close[4], entry));
        row.complete = true;
    }
    Some(row)
}
