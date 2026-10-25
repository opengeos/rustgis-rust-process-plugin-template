//! JSON-line transport shared by commands and tools. Keep stdout for the protocol and
//! use stderr for diagnostics from native libraries.

use serde_json::{Value, json};
use std::io::{BufRead, Read, Write};

/// Maximum JSON line size, including its newline. Bound reads before parsing.
const MAX_LINE: u64 = 128 * 1024 * 1024;

/// A connection to RustGIS, with one flushed JSON object per line.
pub struct Connection<R, W> {
    input: R,
    output: W,
}

impl<R: BufRead, W: Write> Connection<R, W> {
    /// Connect a buffered input and an output stream.
    pub fn new(input: R, output: W) -> Self {
        Self { input, output }
    }

    /// Read one JSON object, rejecting EOF, oversized lines and non-object values.
    pub fn read(&mut self) -> Result<Value, String> {
        let mut line = Vec::new();
        (&mut self.input).take(MAX_LINE + 1).read_until(b'\n', &mut line).map_err(|e| format!("could not read RustGIS input: {e}"))?;
        if line.is_empty() {
            return Err("RustGIS closed its input before sending a JSON object".into());
        }
        if line.len() as u64 > MAX_LINE {
            return Err("RustGIS input exceeds the 128 MiB line limit".into());
        }
        let value: Value = serde_json::from_slice(&line).map_err(|e| format!("invalid RustGIS JSON: {e}"))?;
        if !value.is_object() {
            return Err("RustGIS input must be a JSON object".into());
        }
        Ok(value)
    }

    /// Write and flush a protocol message before waiting for any host reply.
    pub fn send(&mut self, value: &Value) -> Result<(), String> {
        serde_json::to_writer(&mut self.output, value).map_err(|e| format!("could not write plugin output: {e}"))?;
        self.output.write_all(b"\n").and_then(|()| self.output.flush()).map_err(|e| format!("could not flush plugin output: {e}"))
    }

    /// Make a host request. Tools cannot make host calls.
    pub fn call(&mut self, op: &str, params: Value) -> Result<Value, String> {
        self.send(&json!({"call": {"op": op, "params": params}}))?;
        let reply = self.read()?;
        if let Some(error) = reply.get("error").and_then(Value::as_str) {
            return Err(error.into());
        }
        reply.get("ok").cloned().ok_or_else(|| "RustGIS reply has neither ok nor an error string".into())
    }

    /// Publish transient progress while a tool runs.
    pub fn progress(&mut self, text: &str) -> Result<(), String> {
        self.send(&json!({"progress": text}))
    }

    /// Log a message that RustGIS keeps with the run.
    pub fn log(&mut self, text: &str) -> Result<(), String> {
        self.send(&json!({"log": text}))
    }

    /// Finish this invocation with exactly one success or error envelope.
    pub fn finish(&mut self, result: Result<Value, String>) -> Result<(), String> {
        self.send(&match result {
            Ok(value) => json!({"ok": value}),
            Err(error) => json!({"error": error}),
        })
    }
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used)]
    use super::*;
    use std::io::Cursor;

    #[test]
    fn host_calls_use_envelopes_and_preserve_null_results() {
        let mut output = Vec::new();
        let mut connection = Connection::new(Cursor::new(b"{\"ok\":null}\n"), &mut output);
        assert_eq!(connection.call("host.notify", json!({"text": "hello"})).unwrap(), Value::Null);
        let sent: Value = serde_json::from_slice(&output).unwrap();
        assert_eq!(sent, json!({"call": {"op": "host.notify", "params": {"text": "hello"}}}));
    }

    #[test]
    fn host_errors_and_malformed_replies_return_errors() {
        for reply in ["{\"error\":\"permission denied\"}\n", "{}\n", "[]\n", "not json\n", ""] {
            let mut connection = Connection::new(Cursor::new(reply), Vec::new());
            assert!(connection.call("host.layers", json!({})).is_err(), "{reply}");
        }
    }

    #[test]
    fn messages_have_separate_lines_and_a_final_envelope() {
        let mut output = Vec::new();
        let mut connection = Connection::new(Cursor::new(""), &mut output);
        connection.progress("Reading").unwrap();
        connection.log("Done").unwrap();
        connection.finish(Err("bad input".into())).unwrap();
        let lines: Vec<Value> =
            output.split(|&b| b == b'\n').filter(|line| !line.is_empty()).map(|line| serde_json::from_slice(line).unwrap()).collect();
        assert_eq!(lines, [json!({"progress": "Reading"}), json!({"log": "Done"}), json!({"error": "bad input"})]);
    }
}
