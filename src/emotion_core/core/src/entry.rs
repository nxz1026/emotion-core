use pyo3::prelude::*;
use std::collections::HashSet;

/// Entry context — simulates v_entry_ctx view output (Phase 4).
#[pyclass]
#[derive(Clone)]
pub struct EntryCtx {
    #[pyo3(get, set)]
    pub sole: Option<crate::ladder::LadderDay>,
    #[pyo3(get, set)]
    pub rows: Vec<crate::ladder::LadderDay>,
    #[pyo3(get, set)]
    pub y_comp: (i64, i64),
    #[pyo3(get, set)]
    pub y_codes: HashSet<String>,
    #[pyo3(get, set)]
    pub survivors: HashSet<String>,
    #[pyo3(get, set)]
    pub y_peer: i64,
    #[pyo3(get, set)]
    pub window: String,
    #[pyo3(get, set)]
    pub min_days: i64,
    #[pyo3(get, set)]
    pub diverge_min: f64,
    #[pyo3(get, set)]
    pub c5_verifiable: bool,
    #[pyo3(get, set)]
    pub name: String,
    #[pyo3(get, set)]
    pub turnover_rate: Option<f64>,
    #[pyo3(get, set)]
    pub bomb_times: Option<i64>,
}

#[pymethods]
impl EntryCtx {
    #[new]
    #[pyo3(signature = (sole=None, rows=vec![], y_comp=(0,0), y_codes=HashSet::new(), survivors=HashSet::new(), y_peer=0, window="NONE".to_string(), min_days=4, diverge_min=5.0, c5_verifiable=true, name="".to_string(), turnover_rate=None, bomb_times=None))]
    pub fn new(
        sole: Option<crate::ladder::LadderDay>,
        rows: Vec<crate::ladder::LadderDay>,
        y_comp: (i64, i64),
        y_codes: HashSet<String>,
        survivors: HashSet<String>,
        y_peer: i64,
        window: String,
        min_days: i64,
        diverge_min: f64,
        c5_verifiable: bool,
        name: String,
        turnover_rate: Option<f64>,
        bomb_times: Option<i64>,
    ) -> Self {
        EntryCtx { sole, rows, y_comp, y_codes, survivors, y_peer, window, min_days, diverge_min, c5_verifiable, name, turnover_rate, bomb_times }
    }
}

fn _field(ctx: &EntryCtx, cand: &crate::ladder::LadderDay, key: &str, default: &str) -> String {
    match key {
        "name" => if ctx.name.is_empty() { cand.code.clone() } else { ctx.name.clone() },
        _ => default.to_string(),
    }
}

#[pyfunction]
pub fn c1_uniqueness(cand: &crate::ladder::LadderDay, ctx: &EntryCtx) -> (bool, String) {
    let ok = ctx.sole.as_ref().map(|s| cand.code == s.code).unwrap_or(false);
    let name = _field(ctx, cand, "name", "");
    (ok, format!("唯一换手高标: {}({}) {}板", name, cand.code, cand.cont_days))
}

#[pyfunction]
pub fn c2_exchange(cand: &crate::ladder::LadderDay, _ctx: &EntryCtx) -> (bool, String) {
    (cand.is_exchange, if cand.is_exchange { "换手板(非一字)".to_string() } else { "一字板=网络垄断，无有效投票".to_string() })
}

#[pyfunction]
pub fn c3_elimination(cand: &crate::ladder::LadderDay, ctx: &EntryCtx) -> (bool, String) {
    let (g, s) = ctx.y_comp;
    if !(g >= 2 && s == 1) {
        return (false, format!("昨日最高板组{}只→今日幸存{}只(要求≥2竞争且仅1幸存)", g, s));
    }
    if !ctx.y_codes.contains(&cand.code) {
        let name = _field(ctx, cand, "name", "");
        return (false, format!("候选({})不在昨日最高组内——新插队高标，淘汰赛身份不成立", name));
    }
    if ctx.survivors != HashSet::from([cand.code.clone()]) {
        return (false, "幸存者非候选——昨日组唯一幸存者另有其票，候选已被淘汰".to_string());
    }
    (true, format!("昨日最高板组{}只→候选胜出（集合等值：幸存=候选本身）", g))
}

#[pyfunction]
pub fn c4_min_days(cand: &crate::ladder::LadderDay, ctx: &EntryCtx) -> (bool, String) {
    (cand.cont_days >= ctx.min_days, format!("{}板 >= 门槛{}", cand.cont_days, ctx.min_days))
}

#[pyfunction]
pub fn c5_strength_diverge(cand: &crate::ladder::LadderDay, ctx: &EntryCtx) -> (Option<bool>, String) {
    if ctx.window != "ENHANCED" {
        return (Some(true), "标准窗口不适用".to_string());
    }
    if !ctx.c5_verifiable {
        return (None, "⚠ EM池窗口外：回封腿无池数据、换手率系当前股本反算——本条件不可核验(UNKNOWN)，不计入通过/否决".to_string());
    }
    let bomb_times = ctx.bomb_times.unwrap_or(0);
    if bomb_times >= 1 {
        return (Some(true), format!("炸板{}次回封", bomb_times));
    }
    match ctx.turnover_rate {
        None => (Some(true), format!("⚠ 换手率缺失（非 0）——强度补偿无法核验，阈值{}%，建议人工确认", ctx.diverge_min)),
        Some(t) if t.is_nan() => (Some(true), format!("⚠ 换手率缺失（非 0）——强度补偿无法核验，阈值{}%，建议人工确认", ctx.diverge_min)),
        Some(t) => {
            let ok = t >= ctx.diverge_min;
            (Some(ok), format!("换手{:.2}%{}{}阈值{}%", t, if ok { "≥" } else { "<" }, "", ctx.diverge_min))
        }
    }
}

#[pyfunction]
pub fn w1_crowding(_cand: &crate::ladder::LadderDay, ctx: &EntryCtx) -> (bool, String) {
    if ctx.rows.is_empty() {
        return (true, "无同身位扎堆".to_string());
    }
    let h = ctx.rows.iter().map(|r| r.cont_days).max().unwrap_or(0);
    let n = ctx.rows.iter().filter(|r| r.cont_days == h).count();
    if n >= 2 {
        (true, format!("⚠ 同身位扎堆：绝对最高 {}板 {} 只（历史组中位-8.69%/胜率8%，建议降仓或放弃）", h, n))
    } else {
        (true, "无同身位扎堆".to_string())
    }
}

#[pyfunction]
pub fn passed_of(results: Vec<(String, Option<bool>, String)>) -> bool {
    results.iter().filter_map(|(_, ok, _)| *ok).all(|v| v)
}

#[pyfunction]
pub fn split_checklist(results: Vec<(String, Option<bool>, String)>) -> (Vec<(String, bool, String)>, Vec<(String, bool, String)>) {
    let mut conds = Vec::new();
    let mut warns = Vec::new();
    for (label, ok, note) in results {
        if label.starts_with('W') {
            warns.push((label, ok.unwrap_or(true), note));
        } else {
            conds.push((label, ok.unwrap_or(false), note));
        }
    }
    (conds, warns)
}
