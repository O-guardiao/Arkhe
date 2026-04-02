//! Python object → serde_json::Value conversion with inline surrogate sanitisation.
//!
//! Replaces the two-pass Python approach (`_normalize_json_value` + `json.dumps`)
//! with a single Rust traversal that normalises types **and** serialises in one shot.

use pyo3::prelude::*;
use pyo3::types::*;
use serde_json::{Map, Number, Value};

// ---------------------------------------------------------------------------
// String sanitisation
// ---------------------------------------------------------------------------

/// Extract a Python string, replacing lone surrogates (U+D800..U+DFFF) with U+FFFD.
///
/// Fast path: `to_str()` succeeds when the string contains no surrogates (99.9 % of
/// real-world traffic), so the hot path is a single pointer copy with zero allocation
/// inside Rust – the owned `String` is only created when we actually need it for
/// `serde_json::Value::String`.
pub fn sanitize_py_string(s: &Bound<'_, PyString>) -> PyResult<String> {
    match s.to_str() {
        Ok(valid) => Ok(valid.to_owned()),
        Err(_) => {
            // Contains surrogates – encode as UTF-32-LE with surrogatepass to get
            // raw code-points, then replace surrogate code-points with U+FFFD.
            // This matches the Python `_sanitize_surrogates` behaviour exactly.
            let bytes_obj = s.call_method1("encode", ("utf-32-le", "surrogatepass"))?;
            let bytes: &Bound<'_, PyBytes> = bytes_obj.downcast()?;
            let raw = bytes.as_bytes();

            let mut result = String::with_capacity(raw.len() / 4);
            for chunk in raw.chunks_exact(4) {
                let cp = u32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
                if (0xD800..=0xDFFF).contains(&cp) {
                    result.push('\u{FFFD}');
                } else if let Some(c) = char::from_u32(cp) {
                    result.push(c);
                } else {
                    result.push('\u{FFFD}');
                }
            }
            Ok(result)
        }
    }
}

// ---------------------------------------------------------------------------
// Dict-key normalisation (matches Python `_normalize_json_key`)
// ---------------------------------------------------------------------------

fn normalize_key(key: &Bound<'_, PyAny>) -> PyResult<String> {
    if let Ok(s) = key.downcast::<PyString>() {
        return sanitize_py_string(s);
    }
    if key.is_none() {
        return Ok("null".to_owned());
    }
    // bool before int (Python: bool ⊂ int)
    if key.is_instance_of::<PyBool>() {
        return Ok(if key.extract::<bool>()? { "true" } else { "false" }.to_owned());
    }
    if let Ok(i) = key.extract::<i64>() {
        return Ok(i.to_string());
    }
    if let Ok(f) = key.extract::<f64>() {
        return Ok(f.to_string());
    }
    // Fallback: str(key)
    let s = key.str()?;
    sanitize_py_string(&s)
}

// ---------------------------------------------------------------------------
// Recursive PyObject → serde_json::Value
// ---------------------------------------------------------------------------

/// Convert an arbitrary Python object into a `serde_json::Value`, sanitising
/// surrogates in strings and normalising dict keys along the way.
///
/// Type dispatch order mirrors the frequency of types in typical LM wire
/// messages: None → bool → int → float → str → dict → list → (rare types).
pub fn py_to_json_value(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    // ---- High-frequency types ----

    if obj.is_none() {
        return Ok(Value::Null);
    }

    // Bool MUST precede int (Python `bool` is a subclass of `int`).
    if obj.is_instance_of::<PyBool>() {
        return Ok(Value::Bool(obj.extract::<bool>()?));
    }

    if obj.is_instance_of::<PyInt>() {
        return match obj.extract::<i64>() {
            Ok(i) => Ok(Value::Number(Number::from(i))),
            Err(_) => match obj.extract::<u64>() {
                Ok(u) => Ok(Value::Number(Number::from(u))),
                // Oversized int – fall through to string representation.
                Err(_) => Ok(Value::String(obj.str()?.to_str()?.to_owned())),
            },
        };
    }

    if obj.is_instance_of::<PyFloat>() {
        let f: f64 = obj.extract()?;
        // NaN / ±Inf cannot be represented in JSON → null.
        return Ok(Number::from_f64(f).map_or(Value::Null, Value::Number));
    }

    if let Ok(s) = obj.downcast::<PyString>() {
        return Ok(Value::String(sanitize_py_string(s)?));
    }

    if let Ok(d) = obj.downcast::<PyDict>() {
        let mut map = Map::with_capacity(d.len());
        for (k, v) in d.iter() {
            map.insert(normalize_key(&k)?, py_to_json_value(&v)?);
        }
        return Ok(Value::Object(map));
    }

    if let Ok(l) = obj.downcast::<PyList>() {
        let arr: Vec<Value> = l
            .iter()
            .map(|item| py_to_json_value(&item))
            .collect::<PyResult<_>>()?;
        return Ok(Value::Array(arr));
    }

    // ---- Low-frequency types ----

    if let Ok(t) = obj.downcast::<PyTuple>() {
        let arr: Vec<Value> = t
            .iter()
            .map(|item| py_to_json_value(&item))
            .collect::<PyResult<_>>()?;
        return Ok(Value::Array(arr));
    }

    if let Ok(s) = obj.downcast::<PySet>() {
        let arr: Vec<Value> = s
            .iter()
            .map(|item| py_to_json_value(&item))
            .collect::<PyResult<_>>()?;
        return Ok(Value::Array(arr));
    }

    if let Ok(s) = obj.downcast::<PyFrozenSet>() {
        let arr: Vec<Value> = s
            .iter()
            .map(|item| py_to_json_value(&item))
            .collect::<PyResult<_>>()?;
        return Ok(Value::Array(arr));
    }

    if let Ok(b) = obj.downcast::<PyBytes>() {
        return Ok(Value::String(
            String::from_utf8_lossy(b.as_bytes()).into_owned(),
        ));
    }

    if let Ok(ba) = obj.downcast::<PyByteArray>() {
        // SAFETY: we consume the slice immediately; no GIL release occurs in between.
        let bytes = unsafe { ba.as_bytes() };
        return Ok(Value::String(String::from_utf8_lossy(bytes).into_owned()));
    }

    // datetime / date / time – try `isoformat()` for ISO-8601 output.
    // This only fires for objects that passed every type check above, i.e.
    // non-standard types.  The `call_method0` raises AttributeError for
    // objects without `isoformat`, which `if let Ok` silently skips.
    if let Ok(iso) = obj.call_method0("isoformat") {
        if let Ok(s) = iso.downcast::<PyString>() {
            if let Ok(text) = s.to_str() {
                return Ok(Value::String(text.to_owned()));
            }
        }
    }

    // Final fallback: str(obj) – matches Python `_json_default = str`.
    let s = obj.str()?;
    Ok(Value::String(sanitize_py_string(&s)?))
}

// ---------------------------------------------------------------------------
// Tests (pure-Rust, no Python interpreter required)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use serde_json::json;
    use serde_json::Value;

    #[test]
    fn primitives_roundtrip() {
        assert_eq!(serde_json::to_string(&Value::Null).unwrap(), "null");
        assert_eq!(serde_json::to_string(&Value::Bool(true)).unwrap(), "true");
        assert_eq!(serde_json::to_string(&json!(42)).unwrap(), "42");
        assert_eq!(serde_json::to_string(&json!(3.14)).unwrap(), "3.14");
        assert_eq!(
            serde_json::to_string(&json!("hello")).unwrap(),
            "\"hello\""
        );
    }

    #[test]
    fn nested_dict_roundtrip() {
        let val = json!({
            "prompt": "hello",
            "model": "gpt-4",
            "depth": 0,
            "data": [1, 2, 3]
        });
        let bytes = serde_json::to_vec(&val).unwrap();
        let back: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(val, back);
    }

    #[test]
    fn frame_header_encoding() {
        let payload = serde_json::to_vec(&json!({"k": "v"})).unwrap();
        let len_bytes = (payload.len() as u32).to_be_bytes();
        let mut frame = Vec::with_capacity(4 + payload.len());
        frame.extend_from_slice(&len_bytes);
        frame.extend_from_slice(&payload);

        let decoded_len =
            u32::from_be_bytes([frame[0], frame[1], frame[2], frame[3]]) as usize;
        assert_eq!(decoded_len, payload.len());
        let decoded: Value = serde_json::from_slice(&frame[4..]).unwrap();
        assert_eq!(json!({"k": "v"}), decoded);
    }

    #[test]
    fn nan_becomes_null() {
        use serde_json::Number;
        assert!(Number::from_f64(f64::NAN).is_none());
    }

    #[test]
    fn large_payload_roundtrip() {
        let mut obj = serde_json::Map::new();
        for i in 0..1000 {
            obj.insert(format!("key_{i}"), json!(i));
        }
        let val = Value::Object(obj);
        let bytes = serde_json::to_vec(&val).unwrap();
        let back: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(val, back);
    }

    #[test]
    fn unicode_strings_survive_roundtrip() {
        let val = json!({"emoji": "🚀🔥", "cjk": "日本語", "accents": "café résumé"});
        let bytes = serde_json::to_vec(&val).unwrap();
        let back: Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(val, back);
    }
}
