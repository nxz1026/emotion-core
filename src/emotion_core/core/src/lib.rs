use pyo3::prelude::*;

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
    Ok(())
}
