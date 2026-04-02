use std::io::{self, Read};

use arkhe_policy_core::{infer_runtime_execution_policy, PolicyDecisionInput};
use serde_json::{json, Value};


fn main() {
    if let Err(err) = run() {
        eprintln!("{err}");
        std::process::exit(1);
    }
}


fn run() -> Result<(), String> {
    let mut raw_input = String::new();
    io::stdin()
        .read_to_string(&mut raw_input)
        .map_err(|err| format!("failed to read stdin: {err}"))?;

    if raw_input.trim().is_empty() {
        return Err("empty policy request".to_string());
    }

    let input: PolicyDecisionInput = serde_json::from_str(&raw_input)
        .map_err(|err| format!("invalid policy request JSON: {err}"))?;

    let policy = infer_runtime_execution_policy(&input);
    let output = build_response(policy);
    let serialized = serde_json::to_string(&output)
        .map_err(|err| format!("failed to serialize policy response: {err}"))?;

    println!("{serialized}");
    Ok(())
}


fn build_response(policy: arkhe_policy_core::RuntimeExecutionPolicy) -> Value {
    json!({
        "policy_version": 1,
        "task_class": policy.task_class,
        "allow_recursion": policy.allow_recursion,
        "allow_role_orchestrator": policy.allow_role_orchestrator,
        "max_iterations_override": policy.max_iterations_override,
        "root_model_override": policy.root_model_override,
        "note": policy.note,
    })
}