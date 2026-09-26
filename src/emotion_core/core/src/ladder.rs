use pyo3::prelude::*;
use std::collections::HashSet;

#[pyclass]
#[derive(Clone)]
pub struct LadderDay {
    #[pyo3(get, set)]
    pub date: String,
    #[pyo3(get, set)]
    pub code: String,
    #[pyo3(get, set)]
    pub cont_days: i64,
    #[pyo3(get, set)]
    pub is_exchange: bool,
    #[pyo3(get, set)]
    pub is_top: bool,
    #[pyo3(get, set)]
    pub is_sole_top: bool,
    #[pyo3(get, set)]
    pub y_top_group_count: i64,
    #[pyo3(get, set)]
    pub y_top_survivor_count: i64,
}

#[pymethods]
impl LadderDay {
    #[new]
    #[pyo3(signature = (date, code, cont_days, is_exchange, is_top=false, is_sole_top=false, y_top_group_count=0, y_top_survivor_count=0))]
    pub fn new(
        date: String,
        code: String,
        cont_days: i64,
        is_exchange: bool,
        is_top: bool,
        is_sole_top: bool,
        y_top_group_count: i64,
        y_top_survivor_count: i64,
    ) -> Self {
        LadderDay { date, code, cont_days, is_exchange, is_top, is_sole_top, y_top_group_count, y_top_survivor_count }
    }
}

const MIN_LEADER_DAYS: i64 = 4;

#[pyfunction]
pub fn top_group(rows: Vec<LadderDay>) -> Vec<LadderDay> {
    let ex: Vec<&LadderDay> = rows.iter().filter(|r| r.is_exchange).collect();
    if ex.is_empty() { return vec![]; }
    let h = ex.iter().map(|r| r.cont_days).max().unwrap_or(0);
    rows.into_iter().filter(|r| r.is_exchange && r.cont_days == h).collect()
}

#[pyfunction]
pub fn sole_top(rows: Vec<LadderDay>, min_days: Option<i64>) -> Option<LadderDay> {
    let md = min_days.unwrap_or(MIN_LEADER_DAYS);
    let tg = top_group(rows);
    if tg.len() == 1 && tg[0].cont_days >= md { Some(tg[0].clone()) } else { None }
}

#[pyfunction]
pub fn fold_candidates(
    date: String,
    rows: Vec<(String, i64, bool)>,
    prev_rows: Vec<(String, i64, bool)>,
    today_exchange: HashSet<String>,
) -> Vec<LadderDay> {
    let mk = |day: &str, code: &str, days: i64, exchange: bool| LadderDay {
        date: day.to_string(), code: code.to_string(), cont_days: days,
        is_exchange: exchange, is_top: false, is_sole_top: false,
        y_top_group_count: 0, y_top_survivor_count: 0,
    };
    let mut base: Vec<LadderDay> = rows.iter().map(|(c, d, e)| mk(&date, c, *d, *e)).collect();
    base.sort_by(|a, b| b.cont_days.cmp(&a.cont_days).then_with(|| a.code.cmp(&b.code)));
    let top_codes: HashSet<String> = top_group(base.iter().map(|r| r.clone()).collect())
        .iter().map(|r| r.code.clone()).collect();
    let sole = sole_top(base.iter().map(|r| r.clone()).collect(), None);
    let prev_base: Vec<LadderDay> = prev_rows.iter().map(|(c, d, e)| mk(&date, c, *d, *e)).collect();
    let prev_top: HashSet<String> = top_group(prev_base).iter().map(|r| r.code.clone()).collect();
    let group_count = prev_top.len() as i64;
    let survivor_count = prev_top.intersection(&today_exchange).count() as i64;
    base.into_iter().map(|r| LadderDay {
        is_top: top_codes.contains(&r.code),
        is_sole_top: sole.as_ref().map(|s| r.code == s.code).unwrap_or(false),
        y_top_group_count: group_count,
        y_top_survivor_count: survivor_count,
        ..r
    }).collect()
}
