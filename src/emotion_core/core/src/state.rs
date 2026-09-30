use pyo3::prelude::*;

#[pyclass]
#[derive(Clone)]
pub struct DayMetrics {
    #[pyo3(get, set)]
    pub date: String,
    #[pyo3(get, set)]
    pub limit_up_count: i64,
    #[pyo3(get, set)]
    pub bomb_rate: Option<f64>,
    #[pyo3(get, set)]
    pub zt_performance: Option<f64>,
    #[pyo3(get, set)]
    pub max_limit_days: i64,
    #[pyo3(get, set)]
    pub limit_down_count: i64,
    #[pyo3(get, set)]
    pub top_amplitude: Option<f64>,
    #[pyo3(get, set)]
    pub top_broke: Option<bool>,
    #[pyo3(get, set)]
    pub bomb_threshold: f64,
    #[pyo3(get, set)]
    pub has_candidate: bool,
    #[pyo3(get, set)]
    pub tradable_max_days: i64,
    #[pyo3(get, set)]
    pub oneword_ratio: Option<f64>,
}

#[pymethods]
impl DayMetrics {
    #[new]
    #[pyo3(signature = (date, limit_up_count, max_limit_days, limit_down_count, bomb_threshold, has_candidate, tradable_max_days, bomb_rate=None, zt_performance=None, top_amplitude=None, top_broke=None, oneword_ratio=None))]
    pub fn new(
        date: String,
        limit_up_count: i64,
        max_limit_days: i64,
        limit_down_count: i64,
        bomb_threshold: f64,
        has_candidate: bool,
        tradable_max_days: i64,
        bomb_rate: Option<f64>,
        zt_performance: Option<f64>,
        top_amplitude: Option<f64>,
        top_broke: Option<bool>,
        oneword_ratio: Option<f64>,
    ) -> Self {
        DayMetrics {
            date,
            limit_up_count,
            bomb_rate,
            zt_performance,
            max_limit_days,
            limit_down_count,
            top_amplitude,
            top_broke,
            bomb_threshold,
            has_candidate,
            tradable_max_days,
            oneword_ratio,
        }
    }
}

#[pyclass]
#[derive(Clone)]
pub struct EmotionState {
    #[pyo3(get, set)]
    pub date: String,
    #[pyo3(get, set)]
    pub phase: String,
    #[pyo3(get, set)]
    pub buy_window: String,
    #[pyo3(get, set)]
    pub force_liquidate: bool,
    #[pyo3(get, set)]
    pub reason: String,
}

#[pymethods]
impl EmotionState {
    #[new]
    pub fn new(
        date: String,
        phase: String,
        buy_window: String,
        force_liquidate: bool,
        reason: String,
    ) -> Self {
        EmotionState {
            date,
            phase,
            buy_window,
            force_liquidate,
            reason,
        }
    }
}

fn lt(a: Option<f64>, b: f64) -> bool {
    a.is_some_and(|v| v < b)
}

fn gt(a: Option<f64>, b: f64) -> bool {
    a.is_some_and(|v| v > b)
}

const EBB_ZT_PERF: f64 = -2.0;
const EBB_LD_MIN: i64 = 15;
const EBB_LD_MULT: f64 = 2.0;
const CLIMAX_ZT: i64 = 80;
const CLIMAX_AMPLITUDE: f64 = 15.0;
const MIN_LEADER_DAYS: i64 = 4;
const FERMENT_ZT_PERF: f64 = 1.5;
const ICE_MAX_DAYS: i64 = 3;
const ICE_ZT_MAX: i64 = 40;

fn rule_ebb(t: &DayMetrics, y: Option<&DayMetrics>) -> bool {
    let two_day = t.top_broke.unwrap_or(false)
        && y.map(|d| d.top_broke.unwrap_or(false)).unwrap_or(false);
    let perf = lt(t.zt_performance, EBB_ZT_PERF);
    let ld = y
        .map(|d| {
            t.limit_down_count > EBB_LD_MIN
                && (t.limit_down_count as f64) > (d.limit_down_count as f64) * EBB_LD_MULT
        })
        .unwrap_or(false);
    two_day || perf || ld
}

fn rule_climax(t: &DayMetrics) -> bool {
    t.limit_up_count > CLIMAX_ZT
        || gt(t.bomb_rate, t.bomb_threshold)
        || t.top_amplitude.map(|v| v > CLIMAX_AMPLITUDE).unwrap_or(false)
}

fn rule_ferment(t: &DayMetrics, y: Option<&DayMetrics>, b: Option<&DayMetrics>) -> bool {
    t.max_limit_days >= MIN_LEADER_DAYS
        && y.is_some()
        && b.is_some()
        && gt(t.zt_performance, FERMENT_ZT_PERF)
        // SAFETY: y is Some (checked at line 141)
        && gt(y.unwrap().zt_performance, FERMENT_ZT_PERF)
        && t.limit_up_count > {
            // SAFETY: y is Some (checked at line 141)
            y.unwrap().limit_up_count
        }
        // SAFETY: y is Some (checked at line 141), b is Some (checked at line 142)
        && y.unwrap().limit_up_count > b.unwrap().limit_up_count
}

fn rule_ice(t: &DayMetrics, y: Option<&DayMetrics>) -> bool {
    let base = t.max_limit_days <= ICE_MAX_DAYS && t.limit_up_count < ICE_ZT_MAX;
    let rev = y
        .map(|d| lt(d.zt_performance, 0.0) && gt(t.zt_performance, 0.0))
        .unwrap_or(false);
    base && (rev || !t.has_candidate)
}

fn window_of(phase: &str) -> (String, bool) {
    match phase {
        "发酵" => ("STANDARD".to_string(), false),
        "高潮" => ("ENHANCED".to_string(), false),
        _ => ("NONE".to_string(), phase == "退潮"),
    }
}

#[pyfunction]
pub fn classify_series(series: Vec<DayMetrics>) -> Vec<EmotionState> {
    let mut result: Vec<EmotionState> = Vec::new();
    for (i, t) in series.iter().enumerate() {
        let y = if i >= 1 { Some(&series[i - 1]) } else { None };
        let b = if i >= 2 { Some(&series[i - 2]) } else { None };

        let fired: Option<&str> = if rule_ebb(t, y) {
            Some("退潮")
        } else if rule_climax(t) {
            Some("高潮")
        } else if rule_ferment(t, y, b) {
            Some("发酵")
        } else if rule_ice(t, y) {
            Some("冰点")
        } else {
            None
        };

        let phase = match fired {
            Some(name) => name.to_string(),
            None => {
                if let Some(prev) = result.last() {
                    prev.phase.clone()
                } else {
                    "冰点".to_string()
                }
            }
        };

        let (buy_window, force_liq) = window_of(&phase);
        let tag = if fired.is_none() && y.is_none() {
            "·首日基线"
        } else if fired.is_none() {
            "·延续"
        } else {
            ""
        };
        let reason = format!(
            "{}{}(涨停{} 最高{}板)",
            phase, tag, t.limit_up_count, t.max_limit_days
        );
        result.push(EmotionState {
            date: t.date.clone(),
            phase,
            buy_window,
            force_liquidate: force_liq,
            reason,
        });
    }
    result
}
