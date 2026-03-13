//! RLM Rust - High-performance components for Recursive Language Models
//!
//! This crate provides Python bindings via PyO3 for:
//! - Socket communication (50-100x faster)
//! - Async LM handler server (10x latency)
//! - Code block parsing (3-5x faster)

mod comms;
mod parsing;
mod handler;

use pyo3::prelude::*;

/// RLM Rust module - High-performance components for RLM
#[pymodule]
fn rlm_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Communication functions
    m.add_function(wrap_pyfunction!(comms::socket_send_py, m)?)?;
    m.add_function(wrap_pyfunction!(comms::socket_recv_py, m)?)?;
    m.add_function(wrap_pyfunction!(comms::socket_request_py, m)?)?;
    
    // Parsing functions
    m.add_function(wrap_pyfunction!(parsing::find_code_blocks_py, m)?)?;
    m.add_function(wrap_pyfunction!(parsing::find_final_answer_py, m)?)?;
    m.add_function(wrap_pyfunction!(parsing::format_iteration_rs_py, m)?)?;
    m.add_function(wrap_pyfunction!(parsing::compute_hash_py, m)?)?;
    
    // Handler class
    m.add_class::<handler::RustLMHandler>()?;
    
    Ok(())
}
