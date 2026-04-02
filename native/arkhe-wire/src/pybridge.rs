//! PyO3 bindings for the wire-protocol acceleration layer.
//!
//! Exposes three functions to Python:
//!
//! * `wire_json_dumps(obj)` – normalise + serialise → JSON `bytes`
//! * `wire_frame_encode(obj)` – 4-byte big-endian header + JSON `bytes`
//! * `sanitize_surrogates(text)` – replace lone surrogates with U+FFFD

use pyo3::prelude::*;
use pyo3::types::*;

use crate::convert;

/// Normalise a Python object and serialise it to JSON bytes in a single pass.
///
/// This replaces the two-step Python path:
///   `_normalize_json_value(obj)` → `orjson.dumps(normalized)`
/// with one Rust traversal that sanitises surrogates, coerces types and writes
/// JSON – eliminating the intermediate normalised Python dict entirely.
#[pyfunction]
fn wire_json_dumps<'py>(
    py: Python<'py>,
    obj: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyBytes>> {
    let value = convert::py_to_json_value(obj)?;
    let bytes = serde_json::to_vec(&value)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    Ok(PyBytes::new_bound(py, &bytes))
}

/// Encode a Python object as a complete wire frame:
///   `[4-byte big-endian length] [JSON payload]`
///
/// The caller can pass the result directly to `socket.sendall()`.
#[pyfunction]
fn wire_frame_encode<'py>(
    py: Python<'py>,
    obj: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyBytes>> {
    let value = convert::py_to_json_value(obj)?;
    let payload = serde_json::to_vec(&value)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;

    let len = payload.len();
    if len > u32::MAX as usize {
        return Err(pyo3::exceptions::PyOverflowError::new_err(format!(
            "Payload too large: {len} bytes exceeds u32::MAX"
        )));
    }

    let mut frame = Vec::with_capacity(4 + len);
    frame.extend_from_slice(&(len as u32).to_be_bytes());
    frame.extend_from_slice(&payload);

    Ok(PyBytes::new_bound(py, &frame))
}

/// Sanitise a Python string, replacing lone surrogates with U+FFFD.
#[pyfunction]
fn sanitize_surrogates(s: &Bound<'_, PyString>) -> PyResult<String> {
    convert::sanitize_py_string(s)
}

// -----------------------------------------------------------------------
// Module registration
// -----------------------------------------------------------------------

#[pymodule]
fn arkhe_wire(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(wire_json_dumps, m)?)?;
    m.add_function(wrap_pyfunction!(wire_frame_encode, m)?)?;
    m.add_function(wrap_pyfunction!(sanitize_surrogates, m)?)?;
    Ok(())
}
