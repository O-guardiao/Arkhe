from rlm.core.rlm import RLM
from rlm.utils.prompts import build_multimodal_user_prompt, build_user_prompt


def test_build_user_prompt_repl_mode_keeps_repl_contract():
    message = build_user_prompt("resolver tarefa", interaction_mode="repl")
    content = message["content"]
    assert "REPL environment" in content
    assert "don't just provide a final answer yet" in content


def test_build_user_prompt_text_mode_removes_repl_guardrails():
    message = build_user_prompt("resolver tarefa", interaction_mode="text")
    lowered = message["content"].lower()
    assert "don't just provide a final answer yet" not in lowered
    assert "querying sub-llms" not in lowered
    assert "```repl```" not in lowered
    assert "final(...)" in lowered


def test_build_multimodal_user_prompt_text_mode_uses_text_action():
    message = build_multimodal_user_prompt(
        [{"type": "text", "text": "contexto visual"}],
        root_prompt="resolver tarefa",
        interaction_mode="text",
    )
    action = message["content"][-1]["text"].lower()
    assert "querying sub-llms" not in action
    assert "```repl```" not in action
    assert "final(...)" in action


def test_text_mode_recovery_nudge_requests_final_instead_of_repl():
    rlm = RLM(backend_kwargs={"model_name": "test-model"}, interaction_mode="text")
    nudge = rlm._build_recovery_nudge(has_code_blocks=False, has_final=False)
    assert nudge is not None
    lowered = nudge["content"].lower()
    assert "final(...)" in lowered
    assert "```repl```" not in lowered


def test_repl_mode_recovery_nudge_preserves_original_contract():
    rlm = RLM(backend_kwargs={"model_name": "test-model"})
    nudge = rlm._build_recovery_nudge(has_code_blocks=False, has_final=False)
    assert nudge is not None
    assert "```repl```" in nudge["content"]