use pyo3::prelude::*;
use std::collections::HashSet;

pub mod indicators;
pub mod state;
pub mod ladder;
pub mod entry;
pub mod promotion;
pub mod ecosystem;
pub mod accelerate;
pub mod exit;
pub mod theme;
pub mod evaluate;
pub mod outcome;

#[pymodule]
fn emotion_core_rust(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<indicators::Bar>()?;
    m.add_class::<indicators::DerivedBar>()?;
    m.add_function(wrap_pyfunction!(indicators::compute_derived, m)?)?;
    m.add_class::<state::DayMetrics>()?;
    m.add_class::<state::EmotionState>()?;
    m.add_function(wrap_pyfunction!(state::classify_series, m)?)?;
    m.add_class::<ladder::LadderDay>()?;
    m.add_function(wrap_pyfunction!(ladder::top_group, m)?)?;
    m.add_function(wrap_pyfunction!(ladder::sole_top, m)?)?;
    m.add_function(wrap_pyfunction!(ladder::fold_candidates, m)?)?;
    m.add_class::<entry::EntryCtx>()?;
    m.add_function(wrap_pyfunction!(entry::c1_uniqueness, m)?)?;
    m.add_function(wrap_pyfunction!(entry::c2_exchange, m)?)?;
    m.add_function(wrap_pyfunction!(entry::c3_elimination, m)?)?;
    m.add_function(wrap_pyfunction!(entry::c4_min_days, m)?)?;
    m.add_function(wrap_pyfunction!(entry::c5_strength_diverge, m)?)?;
    m.add_function(wrap_pyfunction!(entry::w1_crowding, m)?)?;
    m.add_function(wrap_pyfunction!(entry::passed_of, m)?)?;
    m.add_function(wrap_pyfunction!(entry::split_checklist, m)?)?;
    m.add_class::<promotion::PromotionRow>()?;
    m.add_function(wrap_pyfunction!(promotion::layer_row, m)?)?;
    m.add_function(wrap_pyfunction!(promotion::matrix, m)?)?;
    m.add_class::<accelerate::AccelFacts>()?;
    m.add_function(wrap_pyfunction!(accelerate::hit, m)?)?;
    m.add_function(wrap_pyfunction!(accelerate::baseline_median, m)?)?;
    m.add_function(wrap_pyfunction!(accelerate::top_streak, m)?)?;
    m.add_function(wrap_pyfunction!(accelerate::detect, m)?)?;
    m.add_class::<exit::Holding>()?;
    m.add_class::<exit::MarketStat>()?;
    m.add_class::<exit::SellSignal>()?;
    m.add_function(wrap_pyfunction!(exit::sell_signal, m)?)?;
    m.add_function(wrap_pyfunction!(exit::sell, m)?)?;
    m.add_function(wrap_pyfunction!(exit::suggestions, m)?)?;
    m.add_function(wrap_pyfunction!(theme::is_style, m)?)?;
    m.add_function(wrap_pyfunction!(theme::theme_filter, m)?)?;
    m.add_function(wrap_pyfunction!(theme::row_themes, m)?)?;
    m.add_function(wrap_pyfunction!(theme::group_stats, m)?)?;
    m.add_function(wrap_pyfunction!(theme::group_stats_of, m)?)?;
    m.add_function(wrap_pyfunction!(theme::aggregate, m)?)?;
    m.add_class::<theme::GroupStats>()?;
    m.add_function(wrap_pyfunction!(theme::false_relation, m)?)?;
    m.add_function(wrap_pyfunction!(theme::false_relation_pairs, m)?)?;
    m.add_class::<evaluate::FwdBar>()?;
    m.add_function(wrap_pyfunction!(evaluate::split_rows, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate::ebb_exit, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate::exit_family, m)?)?;
    m.add_function(wrap_pyfunction!(evaluate::rule_ret, m)?)?;
    m.add_class::<outcome::OutcomeRow>()?;
    m.add_function(wrap_pyfunction!(outcome::pct, m)?)?;
    m.add_function(wrap_pyfunction!(outcome::outcome_row, m)?)?;
    Ok(())
}
