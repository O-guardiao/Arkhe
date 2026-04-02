use deunicode::deunicode;

use crate::contracts::{ModelRouteConfig, PolicyDecisionInput, RuntimeExecutionPolicy};

const SIMPLE_VERBS: &[&str] = &[
    "verifique",
    "verificar",
    "liste",
    "listar",
    "mostre",
    "mostrar",
    "conte",
    "contar",
    "quantos",
    "quantas",
    "quais",
    "qual",
    "cheque",
    "confirme",
];

const SIMPLE_TARGETS: &[&str] = &[
    "diretor",
    "memoria",
    "memorias",
    "sessao",
    "sessoes",
    "session",
    "state_dir",
    "memory.db",
    "memory status",
    "status",
    "caminho",
    "path",
    "unificad",
    "global",
    "total",
];

const COMPLEX_MARKERS: &[&str] = &[
    "implemente",
    "implementar",
    "refatore",
    "refatorar",
    "arquitetura",
    "migr",
    "pesquise",
    "pesquisar",
    "analise profunda",
    "compare arquiteturas",
    "workflow",
    "pipeline",
    "sub_rlm",
    "subagent",
    "agente",
    "parallel",
    "paralel",
    "recurs",
    "planeje",
    "projeto",
    "codigo",
];

const SIMPLE_SKILL_NAMES: &[&str] = &["filesystem", "sqlite", "telegram_get_updates"];

fn normalize_text(text: &str) -> String {
    deunicode(text).to_lowercase()
}

pub fn resolve_model_route_config(input: &PolicyDecisionInput) -> ModelRouteConfig {
    let planner_model = input
        .planner_model
        .clone()
        .or_else(|| input.default_model.clone())
        .unwrap_or_else(|| "gpt-4o-mini".to_string());

    let worker_model = input
        .worker_model
        .clone()
        .unwrap_or_else(|| planner_model.clone());

    let evaluator_model = input
        .evaluator_model
        .clone()
        .unwrap_or_else(|| worker_model.clone());

    let fast_model = input
        .fast_model
        .clone()
        .unwrap_or_else(|| worker_model.clone());

    let minirepl_model = input
        .minirepl_model
        .clone()
        .unwrap_or_else(|| fast_model.clone());

    ModelRouteConfig {
        planner_model,
        worker_model,
        evaluator_model,
        fast_model,
        minirepl_model,
    }
}

pub fn infer_runtime_execution_policy(input: &PolicyDecisionInput) -> RuntimeExecutionPolicy {
    let text = normalize_text(input.query_text.trim());
    let routes = resolve_model_route_config(input);

    if text.is_empty() {
        return RuntimeExecutionPolicy {
            task_class: "default".to_string(),
            allow_recursion: true,
            allow_role_orchestrator: true,
            max_iterations_override: None,
            root_model_override: None,
            note: "empty_query".to_string(),
        };
    }

    let expanded_skills: Vec<String> = input
        .expanded_skills
        .iter()
        .map(|skill| normalize_text(skill))
        .collect();

    let has_simple_verb = SIMPLE_VERBS.iter().any(|verb| text.contains(verb));
    let has_simple_target = SIMPLE_TARGETS.iter().any(|target| text.contains(target));
    let has_complex_marker = COMPLEX_MARKERS.iter().any(|marker| text.contains(marker));
    let simple_skill_set = !expanded_skills.is_empty()
        && expanded_skills
            .iter()
            .all(|skill| SIMPLE_SKILL_NAMES.iter().any(|allowed| skill == allowed));
    let asks_for_short_check = has_simple_verb && has_simple_target;

    if (asks_for_short_check || simple_skill_set) && !has_complex_marker {
        return RuntimeExecutionPolicy {
            task_class: "simple_inspect".to_string(),
            allow_recursion: false,
            allow_role_orchestrator: false,
            max_iterations_override: Some(3),
            root_model_override: Some(routes.fast_model),
            note: "simple local verification path".to_string(),
        };
    }

    RuntimeExecutionPolicy {
        task_class: "default".to_string(),
        allow_recursion: true,
        allow_role_orchestrator: true,
        max_iterations_override: None,
        root_model_override: None,
        note: "full recursive runtime path".to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::{infer_runtime_execution_policy, resolve_model_route_config};
    use crate::contracts::PolicyDecisionInput;

    #[test]
    fn uses_fast_model_for_simple_inspection() {
        let input = PolicyDecisionInput {
            query_text: "quais os diretorios de memoria e quantas sessoes sao unificadas? faca verificacoes".to_string(),
            expanded_skills: vec!["filesystem".to_string()],
            default_model: Some("gpt-5.4".to_string()),
            fast_model: Some("gpt-5.4-nano".to_string()),
            ..PolicyDecisionInput::default()
        };

        let policy = infer_runtime_execution_policy(&input);

        assert_eq!(policy.task_class, "simple_inspect");
        assert!(!policy.allow_recursion);
        assert!(!policy.allow_role_orchestrator);
        assert_eq!(policy.max_iterations_override, Some(3));
        assert_eq!(policy.root_model_override.as_deref(), Some("gpt-5.4-nano"));
    }

    #[test]
    fn keeps_recursive_path_for_complex_requests() {
        let input = PolicyDecisionInput {
            query_text: "implemente uma policy de roteamento com subagentes e compare tres arquiteturas".to_string(),
            default_model: Some("gpt-5.4".to_string()),
            ..PolicyDecisionInput::default()
        };

        let policy = infer_runtime_execution_policy(&input);

        assert_eq!(policy.task_class, "default");
        assert!(policy.allow_recursion);
        assert!(policy.allow_role_orchestrator);
        assert!(policy.root_model_override.is_none());
    }

    #[test]
    fn route_config_falls_back_consistently() {
        let input = PolicyDecisionInput {
            default_model: Some("gpt-5.4".to_string()),
            ..PolicyDecisionInput::default()
        };

        let routes = resolve_model_route_config(&input);

        assert_eq!(routes.planner_model, "gpt-5.4");
        assert_eq!(routes.worker_model, "gpt-5.4");
        assert_eq!(routes.evaluator_model, "gpt-5.4");
        assert_eq!(routes.fast_model, "gpt-5.4");
        assert_eq!(routes.minirepl_model, "gpt-5.4");
    }
}