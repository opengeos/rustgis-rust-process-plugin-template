#![allow(clippy::unwrap_used)]

use serde_json::{Value, json};
use std::io::Write;
use std::process::{Command, Stdio};

/// Run the actual executable with the initial request and queued host replies.
fn invoke(input: &str) -> Vec<Value> {
    let mut child =
        Command::new(env!("CARGO_BIN_EXE_plugin")).stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().unwrap();
    child.stdin.take().unwrap().write_all(input.as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(output.stderr.is_empty(), "{}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8(output.stdout).unwrap().lines().map(|line| serde_json::from_str(line).unwrap()).collect()
}

#[test]
fn command_exchanges_host_calls_and_finishes_once() {
    let lines = invoke(concat!(
        "{\"abi\":1,\"kind\":\"command\",\"id\":\"layer-count\",\"params\":{}}\n",
        "{\"ok\":[{\"name\":\"Cities\"},{\"name\":\"Countries\"}]}\n",
        "{\"ok\":null}\n"
    ));
    assert_eq!(
        lines,
        [
            json!({"call": {"op": "host.layers", "params": {}}}),
            json!({"call": {"op": "host.notify", "params": {"text": "The map has 2 layers."}}}),
            json!({"ok": {"layers": 2}})
        ]
    );
}

#[test]
fn host_error_is_returned_without_another_call() {
    let lines = invoke(concat!("{\"abi\":1,\"kind\":\"command\",\"id\":\"layer-count\"}\n", "{\"error\":\"permission denied\"}\n"));
    assert_eq!(lines.len(), 2);
    assert_eq!(lines.last(), Some(&json!({"error":"permission denied"})));
}

#[test]
fn tool_counts_supplied_features_without_host_calls() {
    let request = json!({
        "abi": 1, "kind": "tool", "id": "feature-count",
        "params": {"input": "Selected cities"},
        "layers": {"Selected cities": {"type": "FeatureCollection", "features": [{}, {}]}}
    });
    let lines = invoke(&format!("{request}\n"));
    assert_eq!(
        lines,
        [
            json!({"progress": "Counting features"}),
            json!({"log": "Read 2 features."}),
            json!({"ok": {"layers": [], "messages": ["Selected cities has 2 features."]}})
        ]
    );
}

#[test]
fn malformed_requests_return_protocol_errors_without_panicking() {
    for input in [
        "",
        "garbage\n",
        "[]\n",
        "{}\n",
        "{\"abi\":2,\"kind\":\"command\",\"id\":\"layer-count\"}\n",
        "{\"abi\":1,\"kind\":\"command\",\"id\":\"missing\"}\n",
        "{\"abi\":1,\"kind\":\"tool\",\"id\":\"feature-count\",\"params\":{}}\n",
    ] {
        let lines = invoke(input);
        assert_eq!(lines.iter().filter(|line| line.get("error").is_some()).count(), 1, "{input}");
        assert!(lines.iter().all(|line| line.get("ok").is_none() && line.get("call").is_none()), "{input}");
    }
}
