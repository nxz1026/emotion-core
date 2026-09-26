use pyo3::prelude::*;
use std::collections::HashSet;

pub mod indicators;
pub mod state;
pub mod ladder;
pub mod entry;
pub mod promotion;
pub mod ecosystem;

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
    Ok(())
}
