//! T9 信号质量评估（§1.7，E3）纯逻辑核心：出口规则族与正式收益口径。
//!
//! 语义逐字照搬 emotion_core/algorithms/evaluate.py（其本身逐字照搬
//! lkl/services/evaluate.py）的 _split_rows / _ebb_exit / _exit_family / _rule_ret
//! 四函数纯逻辑部分，判定与口径零改动。SQL 取数（_next_bars / _limit_up_on /
//! ebb_days / _pit_drift）与编排（replay / forward_stats）留在 Python 侧，
//! 不在本模块范围。
//!
//! 与 Python 的契约适配（全部是 IO 适配，不涉判定规则）：
//! - 前向 bar 由 Python 侧取数后以 FwdBar 行传入（对应 _next_bars 的
//!   SELECT date, open, high, low, close）；
//! - 涨停止标记 lim 由 Python 侧查 derived_bar 后以 Vec<bool> 传入
//!   （对应 _limit_up_on 逐日查询，与 nb 逐行对齐）；
//! - ret 费后收益闭包由 Python 侧构建传入（对应 _exit_family 内捕获
//!   entry_p/fee 的 ret）——round 语义与 Python 逐位一致；
//! - 日期一律 ISO "YYYY-MM-DD" 字符串（字典序即时间序）。

// pyo3 0.20 的 `#[pymethods]` 宏在 rustc ≥1.80 会触发 non_local_definitions 误报，抑制之。
#![allow(non_local_definitions)]

use pyo3::prelude::*;
use std::collections::HashMap;

/// 前向 bar（_next_bars 的 SELECT 投影：date/open/high/low/close）。
#[pyclass]
#[derive(Clone)]
pub struct FwdBar {
    /// 交易日，ISO "YYYY-MM-DD"（datetime.date 的 str() 形式，字典序即时间序）。
    #[pyo3(get, set)]
    pub date: String,
    #[pyo3(get, set)]
    pub open: f64,
    #[pyo3(get, set)]
    pub high: f64,
    #[pyo3(get, set)]
    pub low: f64,
    #[pyo3(get, set)]
    pub close: f64,
}

#[pymethods]
impl FwdBar {
    /// 接受 datetime.date（str() → "YYYY-MM-DD"）或 ISO 字符串。
    #[new]
    #[pyo3(signature = (date, open, high, low, close))]
    pub fn new(date: &PyAny, open: f64, high: f64, low: f64, close: f64) -> PyResult<Self> {
        let date: String = date.str()?.extract()?;
        Ok(FwdBar {
            date,
            open,
            high,
            low,
            close,
        })
    }
}

/// Python round(x, n) 语义：十进制正确舍入 + round-half-even。
fn py_round(x: f64, ndigits: u32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", ndigits as usize, x).parse().unwrap_or(x)
}

/// 调用 Python 侧传入的费后收益闭包（对应 _exit_family 内捕获 entry_p/fee 的 ret）。
fn call_ret(ret: &PyAny, price: f64) -> PyResult<f64> {
    ret.call1((price,))?.extract()
}

/// 条件行 / W- 警告行分离（= Python _split_rows，行语义同 lkl entry.split_checklist）。
#[pyfunction]
pub fn split_rows(
    rows: Vec<(String, Option<bool>, String)>,
) -> (
    Vec<(String, Option<bool>, String)>,
    Vec<(String, Option<bool>, String)>,
) {
    let mut conds: Vec<(String, Option<bool>, String)> = Vec::new();
    let mut warns: Vec<(String, Option<bool>, String)> = Vec::new();
    for (label, ok, note) in rows {
        if label.starts_with('W') {
            warns.push((label, ok, note));
        } else {
            conds.push((label, ok, note));
        }
    }
    (conds, warns)
}

/// 信号后首个强制清仓日收盘强平（= Python _ebb_exit 纯逻辑）；清仓日在前向窗外
/// → None（pending）。ebbs：调用方注入的退潮日序列（Python 侧 replay/forward_stats
/// 预取一次）；None 兜底自查（ebb_days SQL）留在 Python 侧。
#[pyfunction]
#[pyo3(signature = (nb, d0, ret, ebbs))]
pub fn ebb_exit(
    nb: Vec<FwdBar>,
    d0: String,
    ret: &PyAny,
    ebbs: Vec<String>,
) -> PyResult<Option<f64>> {
    let ed = ebbs.into_iter().find(|d| d > &d0);
    match ed {
        None => Ok(None),
        Some(ed) => match nb.iter().find(|b| b.date == ed) {
            Some(b) => Ok(Some(call_ret(&ret, b.close)?)),
            None => Ok(None),
        },
    }
}

/// 出口规则族（= Python _exit_family 纯逻辑）。同一 T+1 开盘买入前提，费后%，
/// T+1 制度合法（最早 T+2 卖）：
/// - nb：前向 bar（Python 侧 _next_bars 取数后传入）；
/// - lim：与 nb 逐行对应的涨停止标记（Python 侧 _limit_up_on 查询后传入）；
/// - ebbs：退潮日序列（Python 侧 ebb_days 预取后传入）；
/// - ret：费后收益闭包（Python 侧构建，捕获 entry_p/fee）。
///
/// 返回五腿字典 {A_断板开盘, B_断板收盘, C_两日收盘, D_半仓, E_退潮清仓}：
/// 窗内未触发（窗外仍涨停/断板未出现）或制度非法（单 bar）的腿为 None，
/// 不按窗口末近似。
#[pyfunction]
#[pyo3(signature = (nb, d0, ebbs, lim, ret))]
pub fn exit_family(
    nb: Vec<FwdBar>,
    d0: String,
    ebbs: Vec<String>,
    lim: Vec<bool>,
    ret: &PyAny,
) -> PyResult<HashMap<String, Option<f64>>> {
    let mut fam: HashMap<String, Option<f64>> = HashMap::new();
    if nb.is_empty() {
        return Ok(fam);
    }
    let n = nb.len();
    if n < 2 {
        // V4②：单 bar = 当日买当日卖，制度非法 → 全腿 None（空字典）
        return Ok(fam);
    }
    // fb：首个「前日未涨停」的 bar 下标（i≥1 且 lim[i-1] 为 False）
    let fb = (1..n).find(|&i| !lim[i - 1]);
    let a = match fb {
        Some(i) => Some(call_ret(&ret, nb[i].open)?),
        None => None, // 窗内未断板 → pending
    };
    // brk：首个未涨停 bar；T+1 断板（brk==0）顺延 T+2 收盘
    let brk = (0..n).find(|&i| !lim[i]);
    let si: Option<usize> = match brk {
        Some(0) => Some(1),
        other => other,
    };
    let b = match si {
        Some(i) => Some(call_ret(&ret, nb[i].close)?),
        None => None,
    };
    let c = call_ret(&ret, nb[1].close)?; // n>=2 保证 T+2 合法
    let e = ebb_exit(nb.clone(), d0, ret, ebbs)?;
    // D 半仓 = round(0.5*C + 0.5*B, 2)（收益加权平均再舍入，非价格）
    let d = match b {
        Some(b) => Some(py_round(0.5 * c + 0.5 * b, 2)),
        None => None,
    };
    fam.insert("A_断板开盘".to_string(), a);
    fam.insert("B_断板收盘".to_string(), b);
    fam.insert("C_两日收盘".to_string(), Some(c));
    fam.insert("D_半仓".to_string(), d);
    fam.insert("E_退潮清仓".to_string(), e);
    Ok(fam)
}

/// 正式收益口径（= Python _rule_ret 的 E??A 合成）：E 退潮清仓非 None 取 E，
/// 否则回落 A 断板开盘（E=None 或无退潮/清仓日在窗外时）。
#[pyfunction]
pub fn rule_ret(fam: HashMap<String, Option<f64>>) -> Option<f64> {
    match fam.get("E_退潮清仓") {
        Some(Some(v)) => Some(*v),
        _ => fam.get("A_断板开盘").and_then(|v| *v),
    }
}
