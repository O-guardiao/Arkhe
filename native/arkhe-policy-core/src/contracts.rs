use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ModelRouteConfig {
    pub planner_model: String,
    pub worker_model: String,
    pub evaluator_model: String,
    pub fast_model: String,
    pub minirepl_model: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RuntimeExecutionPolicy {
    pub task_class: String,
    pub allow_recursion: bool,
    pub allow_role_orchestrator: bool,
    pub max_iterations_override: Option<u32>,
    pub root_model_override: Option<String>,
    pub note: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PolicyDecisionInput {
    pub query_text: String,
    pub client_id: String,
    pub expanded_skills: Vec<String>,
    pub default_model: Option<String>,
    pub planner_model: Option<String>,
    pub worker_model: Option<String>,
    pub evaluator_model: Option<String>,
    pub fast_model: Option<String>,
    pub minirepl_model: Option<String>,
}