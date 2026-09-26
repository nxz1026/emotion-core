use pyo3::prelude::*;

/// 昨日每只主板非次新连板股 → 今日结果：(昨日板数, 今日板数, 今日是否换手, 今日涨幅%|None)
pub type Pair = (i64, i64, bool, Option<f64>);

/// 晋级层行输出。
#[pyclass]
#[derive(Clone)]
pub struct PromotionRow {
    #[pyo3(get, set)]
    pub date: String,
    #[pyo3(get, set)]
    pub layer: String,
    #[pyo3(get, set)]
    pub promote_from: i64,
    #[pyo3(get, set)]
    pub promote_nominal: i64,
    #[pyo3(get, set)]
    pub promote_exchange: i64,
    #[pyo3(get, set)]
    pub rate_nominal: Option<f64>,
    #[pyo3(get, set)]
    pub rate_exchange: Option<f64>,
    #[pyo3(get, set)]
    pub divergence: Option<f64>,
    #[pyo3(get, set)]
    pub fail_perf: Option<f64>,
}

#[pymethods]
impl PromotionRow {
    #[new]
    #[pyo3(signature = (date, layer, promote_from, promote_nominal, promote_exchange, rate_nominal=None, rate_exchange=None, divergence=None, fail_perf=None))]
    pub fn new(
        date: String,
        layer: String,
        promote_from: i64,
        promote_nominal: i64,
        promote_exchange: i64,
        rate_nominal: Option<f64>,
        rate_exchange: Option<f64>,
        divergence: Option<f64>,
        fail_perf: Option<f64>,
    ) -> Self {
        PromotionRow {
            date, layer, promote_from, promote_nominal, promote_exchange,
            rate_nominal, rate_exchange, divergence, fail_perf,
        }
    }
}

const PROMOTION_MIN_DENOM: i64 = 3;

const PROMOTION_LAYERS: &[(&str, i64, Option<i64>)] = &[
    ("1->2", 1, Some(1)),
    ("2->3", 2, Some(2)),
    ("3->4", 3, Some(3)),
    ("4->5", 4, Some(4)),
    ("5+->6+", 5, None),
];

fn rate(part: i64, whole: i64, min_denom: i64) -> Option<f64> {
    if whole < min_denom { return None; }
    Some((part as f64 / whole as f64 * 10000.0).round() / 10000.0)
}

#[pyfunction]
#[pyo3(signature = (date, layer, lo, pairs, min_denom, hi=None))]
pub fn layer_row(
    date: String,
    layer: String,
    lo: i64,
    pairs: Vec<Pair>,
    min_denom: i64,
    hi: Option<i64>,
) -> PromotionRow {
    let sel: Vec<&Pair> = pairs.iter().filter(|p| p.0 >= lo && hi.map_or(true, |h| p.0 <= h)).collect();
    let promoted: Vec<&Pair> = sel.iter().filter(|p| p.1 >= p.0 + 1).copied().collect();
    let n_from = sel.len() as i64;
    let n_nom = promoted.len() as i64;
    let n_exch = promoted.iter().filter(|p| p.2).count() as i64;
    let r_nom = rate(n_nom, n_from, min_denom);
    let r_exch = rate(n_exch, n_from, min_denom);
    let div = match (r_nom, r_exch) {
        (Some(rn), Some(re)) => Some(((rn - re) * 10000.0).round() / 10000.0),
        _ => None,
    };
    let failed: Vec<f64> = sel.iter().filter(|p| p.1 < p.0 + 1).filter_map(|p| p.3).collect();
    let fail_perf = if failed.len() < min_denom as usize {
        None
    } else {
        Some((failed.iter().sum::<f64>() / failed.len() as f64 * 10000.0).round() / 10000.0)
    };
    PromotionRow {
        date, layer, promote_from: n_from, promote_nominal: n_nom,
        promote_exchange: n_exch, rate_nominal: r_nom, rate_exchange: r_exch,
        divergence: div, fail_perf,
    }
}

#[pyfunction]
pub fn matrix(pairs: Vec<Pair>) -> Vec<PromotionRow> {
    PROMOTION_LAYERS.iter().map(|(name, lo, hi)| {
        layer_row(String::new(), name.to_string(), *lo, pairs.clone(), PROMOTION_MIN_DENOM, *hi)
    }).collect()
}
