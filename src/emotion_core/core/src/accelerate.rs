//! T15 加速事件（PLAN §1.11 / F2）：一字垄断与高度背离。
//!
//! 语义逐字照搬 lkl/services/accelerate.py（docs/07 §3.4）。
//! Rust 版只算纯逻辑（判定/中位数/断档归零），SQL 取数留在 Python 侧。

use pyo3::prelude::*;

/// 加速事件判定实测值（对应 Python detect 的 facts）。
#[pyclass]
#[derive(Clone)]
pub struct AccelFacts {
    #[pyo3(get, set)]
    pub nominal_h: i64,
    #[pyo3(get, set)]
    pub tradable_h: i64,
    #[pyo3(get, set)]
    pub gap: i64,
    #[pyo3(get, set)]
    pub streak: i64,
    #[pyo3(get, set)]
    pub oneword_ratio: Option<f64>,
    #[pyo3(get, set)]
    pub baseline_ratio: Option<f64>,
    #[pyo3(get, set)]
    pub a1: bool,
    #[pyo3(get, set)]
    pub a2: bool,
    #[pyo3(get, set)]
    pub a3: bool,
}

#[pymethods]
impl AccelFacts {
    #[new]
    #[pyo3(signature = (nominal_h, tradable_h, gap, streak, oneword_ratio, baseline_ratio, a1, a2, a3))]
    pub fn new(
        nominal_h: i64,
        tradable_h: i64,
        gap: i64,
        streak: i64,
        oneword_ratio: Option<f64>,
        baseline_ratio: Option<f64>,
        a1: bool,
        a2: bool,
        a3: bool,
    ) -> Self {
        AccelFacts { nominal_h, tradable_h, gap, streak, oneword_ratio, baseline_ratio, a1, a2, a3 }
    }
}

/// 命中规则：A1 且 (A2 或 A3）。A1 是本质（高度与机会背离），A2/A3 是表现。
#[pyfunction]
pub fn hit(a1: bool, a2: bool, a3: bool) -> bool {
    a1 && (a2 || a3)
}

/// 前 window 个有效 ratio 的中位数（对应 _baseline_ratio 内存滚动分支）。
/// 预热期样本不足窗口长度 → None。
#[pyfunction]
#[pyo3(signature = (ratios, window))]
pub fn baseline_median(ratios: Vec<Option<f64>>, window: usize) -> Option<f64> {
    let vals: Vec<f64> = ratios.into_iter().take(window).filter_map(|r| r).collect();
    if vals.len() < window {
        return None;
    }
    let mut sorted = vals;
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let mid = window / 2;
    let med = if window % 2 == 1 {
        sorted[mid]
    } else {
        (sorted[mid - 1] + sorted[mid]) / 2.0
    };
    Some((med * 10000.0).round() / 10000.0)
}

/// 单票最长连续一字天数（断档归零，对应 _top_streak 序列处理）。
/// `seq` 按市场日历升序，`cal_pos` 为对应交易日在市场日历中的索引。
#[pyfunction]
#[pyo3(signature = (seq, cal_pos))]
pub fn top_streak(seq: Vec<(bool, bool)>, cal_pos: Vec<i64>) -> i64 {
    let mut n = 0i64;
    let mut last_idx: Option<i64> = None;
    for ((ow, lu), idx) in seq.into_iter().zip(cal_pos) {
        if let Some(li) = last_idx {
            if idx - li > 1 {
                n = 0; // 中间存在停牌缺行 → 断档
            }
        }
        n = if ow && lu { n + 1 } else { 0 };
        last_idx = Some(idx);
    }
    n
}

/// 加速事件判定（对应 Python detect 的纯逻辑部分）。
/// 返回 (是否加速, 说明, 实测值)。
#[pyfunction]
#[pyo3(signature = (nominal_h, tradable_h, ratio, baseline, streak, height_gap, oneword_days, oneword_ratio))]
pub fn detect(
    nominal_h: i64,
    tradable_h: i64,
    ratio: Option<f64>,
    baseline: Option<f64>,
    streak: i64,
    height_gap: i64,
    oneword_days: i64,
    oneword_ratio: f64,
) -> (bool, String, AccelFacts) {
    let gap = nominal_h - tradable_h;
    let a1 = gap >= height_gap;
    let a2 = streak >= oneword_days;
    let a3 = ratio
        .and_then(|r| baseline.map(|b| (r, b)))
        .map(|(r, b)| r >= oneword_ratio && r > b)
        .unwrap_or(false);
    let hit = a1 && (a2 || a3);
    let facts = AccelFacts {
        nominal_h, tradable_h, gap, streak,
        oneword_ratio: ratio, baseline_ratio: baseline, a1, a2, a3,
    };
    let ratio_str = ratio.map(|r| format!("{r}")).unwrap_or_else(|| "—".to_string());
    let base_str = baseline.map(|b| format!("{b}")).unwrap_or_else(|| "—".to_string());
    let reason = format!(
        "A1高度背离{gap}(阈{height_gap})={} A2连续一字{streak}日(阈{oneword_days})={} A3一字占比{ratio_str}/基线{base_str}(阈{oneword_ratio})={}",
        if a1 { "✓" } else { "✗" },
        if a2 { "✓" } else { "✗" },
        if a3 { "✓" } else { "✗" },
    );
    (hit, reason, facts)
}
