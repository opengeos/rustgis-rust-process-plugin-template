//! A native Rust process plugin: one command, one tool, and no Python runtime.

mod protocol;

use protocol::Connection;
use serde_json::{Value, json};
use std::io::{self, BufRead, Write};

/// Dispatch one ABI request. Keep application logic here and transport in protocol.rs.
fn handle<R: BufRead, W: Write>(request: &Value, host: &mut Connection<R, W>) -> Result<Value, String> {
    if request.get("abi").and_then(Value::as_u64) != Some(1) {
        return Err("request needs ABI version 1".into());
    }
    let kind = request.get("kind").and_then(Value::as_str).ok_or("request needs kind")?;
    let id = request.get("id").and_then(Value::as_str).ok_or("request needs id")?;
    match (kind, id) {
        ("command", "layer-count") => {
            let layers = host.call("host.layers", json!({}))?;
            let count = layers.as_array().ok_or("host.layers did not return an array")?.len();
            host.call("host.notify", json!({"text": format!("The map has {count} layers.")}))?;
            Ok(json!({"layers": count}))
        }
        ("tool", "feature-count") => {
            // Tools receive selected features in request.layers and make no host calls.
            host.progress("Counting features")?;
            let name = request.get("params").and_then(|p| p.get("input")).and_then(Value::as_str).ok_or("input layer is required")?;
            let features = request
                .get("layers")
                .and_then(|l| l.get(name))
                .and_then(|l| l.get("features"))
                .and_then(Value::as_array)
                .ok_or("input layer has no supplied features")?;
            let count = features.len();
            host.log(&format!("Read {count} features."))?;
            Ok(json!({"layers": [], "messages": [format!("{name} has {count} features.")]}))
        }
        _ => Err(format!("unknown {kind} '{id}'")),
    }
}

/// Serve a single invocation, reporting input and application errors over the protocol.
fn serve<R: BufRead, W: Write>(mut host: Connection<R, W>) -> Result<(), String> {
    let result = host.read().and_then(|request| handle(&request, &mut host));
    host.finish(result)
}

fn main() {
    let (input, output) = (io::stdin(), io::stdout());
    if let Err(error) = serve(Connection::new(input.lock(), output.lock())) {
        eprintln!("{error}");
        std::process::exit(1);
    }
}
