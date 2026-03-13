from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from rlm.core.handoff import HandoffRecord, make_handoff_fn
from rlm.core.skill_telemetry import SkillTelemetryStore


class TestHandoffRecord:
    def test_normaliza_target_role(self):
        record = HandoffRecord(
            target_role="Evaluator-Agent",
            reason="falha recorrente",
            remaining_goal="validar a saida final",
        )
        assert record.target_role == "evaluator"

    def test_exige_reason(self):
        with pytest.raises(ValueError):
            HandoffRecord(target_role="worker", reason="", remaining_goal="continuar")

    def test_exige_remaining_goal(self):
        with pytest.raises(ValueError):
            HandoffRecord(target_role="worker", reason="sem acesso", remaining_goal="")

    def test_suggested_mode_invalido_falha(self):
        with pytest.raises(ValueError):
            HandoffRecord(
                target_role="worker",
                reason="precisa executar",
                remaining_goal="fazer deploy",
                suggested_mode="manual",
            )


class TestMakeHandoffFn:
    def test_registra_evento_hook_e_telemetria(self):
        log_event = MagicMock()
        hooks = MagicMock()
        telemetry = SkillTelemetryStore(load_existing=False)
        telemetry.reset()
        sink = []

        request_handoff = make_handoff_fn(
            session_id="sess-1",
            log_event=log_event,
            hooks=hooks,
            telemetry=telemetry,
            client_id="cli-1",
            state_sink=lambda payload: sink.append(payload),
        )

        result = request_handoff(
            "worker",
            reason="triagem concluida",
            remaining_goal="executar no terminal",
            summary="usuario pediu deploy",
            attempted_skills=["shell", "github"],
            failures=["missing env GITHUB_TOKEN"],
            suggested_mode="focused",
        )

        assert result["ok"] is True
        assert result["handoff"]["target_role"] == "worker"
        log_event.assert_called_once()
        hooks.trigger.assert_called_once()
        assert sink and sink[0]["target_role"] == "worker"
        summary = telemetry.get_summary(include_recent=True, limit=5)
        assert summary["handoff_events"] == 1
        assert summary["recent_events"][0]["event_type"] == "handoff"

    def test_expoe_dados_para_recuperacao_lexical(self):
        telemetry = SkillTelemetryStore(load_existing=False)
        telemetry.reset()
        request_handoff = make_handoff_fn(
            session_id="sess-2",
            log_event=lambda *_args, **_kwargs: None,
            telemetry=telemetry,
        )

        request_handoff(
            "evaluator",
            reason="falha apos shell",
            remaining_goal="verificar resultado do deploy",
            attempted_skills=["shell"],
            failures=["timeout no comando"],
        )

        traces = telemetry.get_relevant_traces("deploy shell timeout", event_type="handoff")
        assert traces
        assert traces[0]["payload"]["target_role"] == "evaluator"