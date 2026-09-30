// emotion-core 生态评级（T16 / F4）：龙空龙策略能否在当前生态工作。
// Python 参考实现：algorithms/dragon_env.py（465 行）。
// 本文件移植 g1~g4 + b1~b5 + _verdict + rate + ladder_health + promotion_strength。
// 设计原则：
//   - 纯判定逻辑（无 IO / 无 SQL）；数据由 Python 侧 query_df 后传入。
//   - 返回值逐字段与 Python 版同形，oracle 测试可直接比对。
//   - Status = Option<bool>：Some(true) 成立 / Some(false) 不成立 / None UNKNOWN。

use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};

// ── 常量（与 dragon_env.py::_cfg 默认值同值）──────────────────────────────────

const DRAGON_H_EXPAND_DAYS: usize = 3;
const DRAGON_HEIGHT_REF_WINDOW: usize = 20;
const DRAGON_G4_HEADROOM: i64 = 1;
const DRAGON_FEEDBACK_LOOKBACK: usize = 5;
const DRAGON_G3_MIN_PERF: f64 = -3.0;
const DRAGON_B3_MAX_PERF: f64 = -7.0;
const DRAGON_B2_DIVERGENCE: f64 = 0.3;
const DRAGON_G2_MIN_MEMBERS: i64 = 3;

// ── 返回结构体 ─────────────────────────────────────────────────────────────────

/// 单项判定结果。
#[pyclass]
#[derive(Clone)]
pub struct EcosystemItem {
    #[pyo3(get, set)]
    pub cond: String,
    #[pyo3(get, set)]
    pub ok: Option<bool>,
    #[pyo3(get, set)]
    pub note: String,
}

#[pymethods]
impl EcosystemItem {
    #[new]
    #[pyo3(signature = (cond, ok, note))]
    pub fn new(cond: String, ok: Option<bool>, note: String) -> Self {
        EcosystemItem { cond, ok, note }
    }
}

/// rate() 的完整返回。
#[pyclass]
#[derive(Clone)]
pub struct EcosystemRating {
    #[pyo3(get, set)]
    pub rating: String,
    #[pyo3(get, set)]
    pub goods: Vec<EcosystemItem>,
    #[pyo3(get, set)]
    pub bads: Vec<EcosystemItem>,
}

#[pymethods]
impl EcosystemRating {
    #[new]
    pub fn new(rating: String, goods: Vec<EcosystemItem>, bads: Vec<EcosystemItem>) -> Self {
        EcosystemRating { rating, goods, bads }
    }
}

/// ladder_health() 返回的梯队健康度。
#[pyclass]
#[derive(Clone)]
pub struct LadderHealth {
    #[pyo3(get, set)]
    pub rows: usize,
    #[pyo3(get, set)]
    pub height_nominal: Option<i64>,
    #[pyo3(get, set)]
    pub height_exchange: Option<i64>,
    #[pyo3(get, set)]
    pub groups_keys: Vec<i64>,
    #[pyo3(get, set)]
    pub groups_vals: Vec<i64>,
    #[pyo3(get, set)]
    pub gaps: Vec<i64>,
    #[pyo3(get, set)]
    pub sole_top: Option<String>,
    #[pyo3(get, set)]
    pub top_group: Vec<String>,
}

#[pymethods]
impl LadderHealth {
    #[new]
    #[pyo3(signature = (rows, height_nominal=None, height_exchange=None, groups_keys=None, groups_vals=None, gaps=None, sole_top=None, top_group=None))]
    pub fn new(
        rows: usize,
        height_nominal: Option<i64>,
        height_exchange: Option<i64>,
        groups_keys: Option<Vec<i64>>,
        groups_vals: Option<Vec<i64>>,
        gaps: Option<Vec<i64>>,
        sole_top: Option<String>,
        top_group: Option<Vec<String>>,
    ) -> Self {
        LadderHealth {
            rows,
            height_nominal,
            height_exchange,
            groups_keys: groups_keys.unwrap_or_default(),
            groups_vals: groups_vals.unwrap_or_default(),
            gaps: gaps.unwrap_or_default(),
            sole_top,
            top_group: top_group.unwrap_or_default(),
        }
    }
}

/// promotion_strength() 的单层行。
#[pyclass]
#[derive(Clone)]
pub struct PromotionLayer {
    #[pyo3(get, set)]
    pub layer: String,
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
    #[pyo3(get, set)]
    pub delta_exchange: Option<f64>,
}

#[pymethods]
impl PromotionLayer {
    #[new]
    #[pyo3(signature = (layer, promote_nominal, promote_exchange, rate_nominal=None, rate_exchange=None, divergence=None, fail_perf=None, delta_exchange=None))]
    pub fn new(
        layer: String,
        promote_nominal: i64,
        promote_exchange: i64,
        rate_nominal: Option<f64>,
        rate_exchange: Option<f64>,
        divergence: Option<f64>,
        fail_perf: Option<f64>,
        delta_exchange: Option<f64>,
    ) -> Self {
        PromotionLayer {
            layer,
            promote_nominal,
            promote_exchange,
            rate_nominal,
            rate_exchange,
            divergence,
            fail_perf,
            delta_exchange,
        }
    }
}

/// promotion_strength() 的完整返回。
#[pyclass]
#[derive(Clone)]
pub struct PromotionStrength {
    #[pyo3(get, set)]
    pub layers: Vec<PromotionLayer>,
    #[pyo3(get, set)]
    pub deep_layer: Option<String>,
    #[pyo3(get, set)]
    pub deep_rate_nominal: Option<f64>,
    #[pyo3(get, set)]
    pub deep_rate_exchange: Option<f64>,
    #[pyo3(get, set)]
    pub deep_delta_exchange: Option<f64>,
}

#[pymethods]
impl PromotionStrength {
    #[new]
    pub fn new(
        layers: Vec<PromotionLayer>,
        deep_layer: Option<String>,
        deep_rate_nominal: Option<f64>,
        deep_rate_exchange: Option<f64>,
        deep_delta_exchange: Option<f64>,
    ) -> Self {
        PromotionStrength {
            layers,
            deep_layer,
            deep_rate_nominal,
            deep_rate_exchange,
            deep_delta_exchange,
        }
    }
}

// ── 判定函数 ──────────────────────────────────────────────────────────────────

/// G1 可交易高度扩张：近 N 日 H 单调不降且至少一日上升。
#[pyfunction]
#[pyo3(signature = (h_series, expand_days=DRAGON_H_EXPAND_DAYS))]
pub fn g1_height_expanding(
    h_series: Vec<i64>,
    expand_days: usize,
) -> (Option<bool>, String) {
    if h_series.len() < expand_days {
        return (None, format!("H 序列仅 {} 日，不足 {}", h_series.len(), expand_days));
    }
    let ok = h_series.windows(2).all(|w| w[1] >= w[0])
        // SAFETY: h_series.len() >= expand_days ≥ 1 (checked above)
        && h_series.last().unwrap() > h_series.first().unwrap();
    let h_str = h_series.iter().map(|v| v.to_string()).collect::<Vec<_>>().join("→");
    (Some(ok), format!("H {h_str}"))
}

/// G4 胜者上方有空间：H <= 近 N 日最高 - HEADROOM。
#[pyfunction]
#[pyo3(signature = (h_series, headroom=DRAGON_G4_HEADROOM, ref_window=DRAGON_HEIGHT_REF_WINDOW))]
pub fn g4_headroom(
    h_series: Vec<i64>,
    headroom: i64,
    ref_window: usize,
) -> (Option<bool>, String) {
    if h_series.len() < ref_window / 2 {
        return (None, format!("参照窗口仅 {} 日", h_series.len()));
    }
    // SAFETY: h_series.len() >= ref_window / 2 ≥ 1 (checked above)
    let h = *h_series.last().unwrap();
    let max_ref = *h_series.iter().max().unwrap();
    let ok = h <= max_ref - headroom;
    (Some(ok), format!("H={h} 近{ref_window}日最高={max_ref}"))
}

/// G2 存在梯队完整主线：主题材库中是否存在成员数达标的主线题材。
/// 首板维度未实现 → 返回 None（UNKNOWN）。
#[pyfunction]
#[pyo3(signature = (theme_rows, min_members=DRAGON_G2_MIN_MEMBERS))]
pub fn g2_theme_ladder(
    theme_rows: Vec<(String, i64, i64, i64)>,
    min_members: i64,
) -> (Option<bool>, String) {
    let qualified: Vec<&(String, i64, i64, i64)> = theme_rows
        .iter()
        .filter(|(_, mc, _, _)| *mc >= min_members)
        .collect();
    if qualified.is_empty() {
        if theme_rows.is_empty() {
            return (None, "theme_group 当日无数据".to_string());
        }
        // SAFETY: theme_rows is non-empty (checked at line 248)
        let top = theme_rows
            .iter()
            .max_by_key(|(_, mc, _, hb)| (*hb, *mc))
            .unwrap();
        return (
            Some(false),
            format!(
                "最强题材 {} 成员{}（<{}，无达标主线）",
                top.0, top.1, min_members
            ),
        );
    }
    // SAFETY: qualified is non-empty (line 247 returns early if empty)
    let best = qualified.iter().max_by_key(|(_, _, _, hb)| *hb).unwrap();
    (
        None,
        format!(
            "主线题材 {} 成员{} 达标（首板维度未实现 P1.5，首板完整性不计——G2 记 UNKNOWN 不证伪，非「尚无数据」)",
            best.0, best.1
        ),
    )
}

/// G3 断板负反馈温和：最高层晋级失败股近 N 日平均跌幅 > 阈值。
#[pyfunction]
#[pyo3(signature = (fail_perfs, min_perf=DRAGON_G3_MIN_PERF))]
pub fn g3_break_feedback(
    fail_perfs: Vec<f64>,
    min_perf: f64,
) -> (Option<bool>, String) {
    if fail_perfs.len() < 2 {
        return (None, format!("断板反馈样本 {} 不足", fail_perfs.len()));
    }
    let avg = fail_perfs.iter().sum::<f64>() / fail_perfs.len() as f64;
    let avg_r = (avg * 100.0).round() / 100.0;
    (
        Some(avg_r > min_perf),
        format!("最高层失败股均涨幅 {avg_r:.1}%"),
    )
}

/// B1 加速事件命中。
#[pyfunction]
pub fn b1_acceleration(accel_hit: bool) -> (Option<bool>, String) {
    (
        Some(accel_hit),
        if accel_hit {
            "加速事件命中".to_string()
        } else {
            "无加速事件".to_string()
        },
    )
}

/// B2 名义高度主要由一字制造：A3 命中且当日最大背离达标。
#[pyfunction]
#[pyo3(signature = (a3_hit, max_divergence, threshold=DRAGON_B2_DIVERGENCE))]
pub fn b2_oneword_made(
    a3_hit: bool,
    max_divergence: Option<f64>,
    threshold: f64,
) -> (Option<bool>, String) {
    if !a3_hit {
        return (Some(false), "A3 未命中".to_string());
    }
    match max_divergence {
        None => (None, "背离度无数据".to_string()),
        Some(d) => {
            let ok = d >= threshold;
            (Some(ok), format!("最大背离 {:.2}", d))
        }
    }
}

/// B3 胜出次日即核按钮：近 N 日 sole_top 次日平均跌幅 < 阈值。
#[pyfunction]
#[pyo3(signature = (avg, count, threshold=DRAGON_B3_MAX_PERF, mode="live"))]
pub fn b3_next_day_dump(
    avg: f64,
    count: usize,
    threshold: f64,
    mode: &str,
) -> (Option<bool>, String) {
    if count < 2 {
        return (None, format!("胜出者次日样本 {} 不足", count));
    }
    let avg_r = (avg * 100.0).round() / 100.0;
    let note = if mode != "live" {
        format!("近{count}次胜出次日均涨幅 {avg_r}%（复盘态：读到次日收盘，当日实盘不可见）")
    } else {
        format!("近{count}次胜出次日均涨幅 {avg_r}%")
    };
    (Some(avg_r < threshold), note)
}

/// B4 无板块支持：换手最高板组各票题材全孤立。
#[pyfunction]
pub fn b4_no_sector(
    rows: Vec<(String, Option<String>, i64)>,
) -> (Option<bool>, String) {
    if rows.is_empty() {
        return (None, "换手最高板组无数据（梯队断层或 theme 未跑）".to_string());
    }
    let supported: Vec<&(String, Option<String>, i64)> = rows
        .iter()
        .filter(|(_, th, mc)| th.is_some() && *mc >= 2)
        .collect();
    if !supported.is_empty() {
        // SAFETY: supported is non-empty (line 361 checks !supported.is_empty())
        let best = supported.iter().max_by_key(|(_, _, mc)| *mc).unwrap();
        return (
            Some(false),
            format!(
                "换手最高板 {}({}) 成员{}——板块有跟随",
                best.0,
                // SAFETY: best.1 is Some (filter at line 359 checks th.is_some())
                best.1.as_ref().unwrap(),
                best.2
            ),
        );
    }
    let no_theme: Vec<String> = rows
        .iter()
        .filter(|(_, th, _)| th.is_none())
        .map(|(c, _, _)| c.clone())
        .collect();
    if !no_theme.is_empty() {
        return (
            None,
            format!(
                "换手最高板 {} 无题材标签（theme 未覆盖），B4 记 UNKNOWN",
                no_theme.join(",")
            ),
        );
    }
    let parts: Vec<String> = rows
        .iter()
        .map(|(c, th, mc)| format!("{}({})成员{}", c, th.as_deref().unwrap_or("—"), mc))
        .collect();
    (Some(true), format!("换手最高板全孤立：{}", parts.join("、")))
}

/// B5 多高标跨题材：同身位组可信题材唯一值 >1。
#[pyfunction]
pub fn b5_cross_theme(
    distinct_themes: i64,
    total_count: usize,
    confident_count: usize,
) -> (Option<bool>, String) {
    if total_count == 0 {
        return (None, "当日无同身位组".to_string());
    }
    if confident_count == 0 {
        return (
            None,
            format!(
                "同身位 {total_count} 只均无可信题材标注（confidence<0.6 弱映射不计），B5 记 UNKNOWN"
            ),
        );
    }
    (
        Some(distinct_themes > 1),
        format!("同身位 {total_count} 只（可信标注 {confident_count}）跨 {distinct_themes} 题材"),
    )
}

// ── 裁决与汇总 ────────────────────────────────────────────────────────────────

fn is_core(name: &str) -> bool {
    name.starts_with("G1") || name.starts_with("G4")
}

/// 分级裁决。
#[pyfunction]
pub fn verdict(
    goods: Vec<(String, Option<bool>, String)>,
    bads: Vec<(String, Option<bool>, String)>,
) -> String {
    if bads.iter().any(|(_, s, _)| matches!(s, Some(true))) {
        return "UNFAVORABLE".to_string();
    }
    if goods.iter().any(|(_, s, _)| matches!(s, Some(false))) {
        return "NEUTRAL".to_string();
    }
    let core_statuses: Vec<Option<bool>> = goods
        .iter()
        .filter(|(n, _, _)| is_core(n))
        .map(|(_, s, _)| *s)
        .collect();
    if !core_statuses.is_empty() && core_statuses.iter().all(|s| matches!(s, Some(true))) {
        return "FAVORABLE".to_string();
    }
    "NEUTRAL".to_string()
}

/// rate() 完整评级。
#[pyfunction]
#[pyo3(signature = (
    g1_h_series,
    g4_h_series,
    theme_rows,
    fail_perfs,
    accel_hit,
    a3_hit,
    max_divergence,
    b3_avg,
    b3_count,
    b4_rows,
    b5_distinct,
    b5_total,
    b5_confident,
    mode="live"
))]
pub fn rate(
    g1_h_series: Vec<i64>,
    g4_h_series: Vec<i64>,
    theme_rows: Vec<(String, i64, i64, i64)>,
    fail_perfs: Vec<f64>,
    accel_hit: bool,
    a3_hit: bool,
    max_divergence: Option<f64>,
    b3_avg: f64,
    b3_count: usize,
    b4_rows: Vec<(String, Option<String>, i64)>,
    b5_distinct: i64,
    b5_total: usize,
    b5_confident: usize,
    mode: &str,
) -> EcosystemRating {
    let (g1_ok, g1_note) = g1_height_expanding(g1_h_series, DRAGON_H_EXPAND_DAYS);
    let (g2_ok, g2_note) = g2_theme_ladder(theme_rows, DRAGON_G2_MIN_MEMBERS);
    let (g3_ok, g3_note) = g3_break_feedback(fail_perfs, DRAGON_G3_MIN_PERF);
    let (g4_ok, g4_note) = g4_headroom(g4_h_series, DRAGON_G4_HEADROOM, DRAGON_HEIGHT_REF_WINDOW);
    let (b1_ok, b1_note) = b1_acceleration(accel_hit);
    let (b2_ok, b2_note) = b2_oneword_made(a3_hit, max_divergence, DRAGON_B2_DIVERGENCE);
    let (b3_ok, b3_note) = b3_next_day_dump(b3_avg, b3_count, DRAGON_B3_MAX_PERF, mode);
    let (b4_ok, b4_note) = b4_no_sector(b4_rows);
    let (b5_ok, b5_note) = b5_cross_theme(b5_distinct, b5_total, b5_confident);

    let goods = vec![
        EcosystemItem::new("G1 可交易高度扩张".to_string(), g1_ok, g1_note),
        EcosystemItem::new("G2 主线梯队完整".to_string(), g2_ok, g2_note),
        EcosystemItem::new("G3 断板负反馈温和".to_string(), g3_ok, g3_note),
        EcosystemItem::new("G4 胜者上方有空间".to_string(), g4_ok, g4_note),
    ];
    let bads = vec![
        EcosystemItem::new("B1 加速事件".to_string(), b1_ok, b1_note),
        EcosystemItem::new("B2 高度靠一字制造".to_string(), b2_ok, b2_note),
        EcosystemItem::new("B3 胜出次日核按钮".to_string(), b3_ok, b3_note),
        EcosystemItem::new("B4 无板块支持".to_string(), b4_ok, b4_note),
        EcosystemItem::new("B5 高标跨题材".to_string(), b5_ok, b5_note),
    ];

    let rating = verdict(
        goods
            .iter()
            .map(|i| (i.cond.clone(), i.ok, i.note.clone()))
            .collect(),
        bads
            .iter()
            .map(|i| (i.cond.clone(), i.ok, i.note.clone()))
            .collect(),
    );

    EcosystemRating { rating, goods, bads }
}

// ── 只读汇总视图 ──────────────────────────────────────────────────────────────

/// ladder_health：梯队健康度。
#[pyfunction]
pub fn ladder_health(
    rows: Vec<(String, i64, bool, bool)>,
) -> LadderHealth {
    if rows.is_empty() {
        return LadderHealth {
            rows: 0,
            height_nominal: None,
            height_exchange: None,
            groups_keys: vec![],
            groups_vals: vec![],
            gaps: vec![],
            sole_top: None,
            top_group: vec![],
        };
    }

    let mut groups: HashMap<i64, i64> = HashMap::new();
    for (_, cont, _, _) in &rows {
        *groups.entry(*cont).or_insert(0) += 1;
    }

    // SAFETY: groups is non-empty (built from rows, checked non-empty above)
    let height_nominal = *groups.keys().max().unwrap();

    let ex_levels: Vec<i64> = rows
        .iter()
        .filter(|(_, _, is_ex, _)| *is_ex)
        .map(|(_, cont, _, _)| *cont)
        .collect();
    let height_exchange = if ex_levels.is_empty() {
        None
    } else {
        // SAFETY: ex_levels is non-empty (line 553 checks is_empty)
        Some(*ex_levels.iter().max().unwrap())
    };

    let gaps = match height_exchange {
        Some(he) => {
            let ex_set: HashSet<i64> = ex_levels.iter().cloned().collect();
            (2..=he).filter(|lv| !ex_set.contains(lv)).collect()
        }
        None => vec![],
    };

    let sole_top = rows
        .iter()
        .find(|(_, _, _, is_sole)| *is_sole)
        .map(|(code, _, _, _)| code.clone());

    let top_group: Vec<String> = match height_exchange {
        Some(he) => rows
            .iter()
            .filter(|(_, cont, is_ex, _)| *is_ex && *cont == he)
            .map(|(code, _, _, _)| code.clone())
            .collect(),
        None => vec![],
    };

    let mut group_pairs: Vec<(i64, i64)> = groups.into_iter().collect();
    group_pairs.sort_by(|a, b| b.0.cmp(&a.0));
    let (keys, vals): (Vec<i64>, Vec<i64>) = group_pairs.into_iter().unzip();

    LadderHealth {
        rows: rows.len(),
        height_nominal: Some(height_nominal),
        height_exchange,
        groups_keys: keys,
        groups_vals: vals,
        gaps,
        sole_top,
        top_group,
    }
}

/// promotion_strength：晋级强度。
#[pyfunction]
pub fn promotion_strength(
    layers_input: Vec<(String, i64, i64, Option<f64>, Option<f64>, Option<f64>, Option<f64>)>,
    prior_rates: Vec<(String, Option<f64>)>,
) -> PromotionStrength {
    let prior_map: HashMap<String, Option<f64>> = prior_rates.into_iter().collect();

    let mut layers: Vec<PromotionLayer> = Vec::new();
    let mut deep: Option<PromotionLayer> = None;

    for (layer, pn, ex, rn, re, div, fp) in &layers_input {
        let delta = match (re, prior_map.get(layer).unwrap_or(&None)) {
            (Some(r), Some(p)) => Some(((r - p) * 10000.0).round() / 10000.0),
            _ => None,
        };
        let pl = PromotionLayer {
            layer: layer.clone(),
            promote_nominal: *pn,
            promote_exchange: *ex,
            rate_nominal: *rn,
            rate_exchange: *re,
            divergence: *div,
            fail_perf: *fp,
            delta_exchange: delta,
        };
        if re.is_some() {
            deep = Some(pl.clone());
        }
        layers.push(pl);
    }

    PromotionStrength {
        layers,
        deep_layer: deep.as_ref().map(|d| d.layer.clone()),
        deep_rate_nominal: deep.as_ref().map(|d| d.rate_nominal).flatten(),
        deep_rate_exchange: deep.as_ref().map(|d| d.rate_exchange).flatten(),
        deep_delta_exchange: deep.as_ref().map(|d| d.delta_exchange).flatten(),
    }
}
