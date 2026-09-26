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
    Ok(())
}
