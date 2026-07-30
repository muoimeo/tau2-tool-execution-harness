"""Deterministic ARC-01A evidence for native v0.1.3 error-budget behavior.

This test does not use an LLM, gateway, Retail tool, or A1 runtime component.
It fixes the currently observed behavior without making a claim about intent.
"""

from tau2.data_model.message import ToolMessage
from tau2.data_model.simulation import TerminationReason
from tau2.orchestrator.orchestrator import Orchestrator


def test_native_tool_errors_do_not_update_num_errors(monkeypatch, base_task):
    orchestrator = Orchestrator(
        domain="arc01-fixture",
        agent=object(),
        user=object(),
        environment=object(),
        task=base_task,
        max_steps=10,
        max_errors=1,
    )

    def fake_initialize():
        return None

    def fake_step():
        orchestrator.trajectory.append(
            ToolMessage(
                id=f"fixture-error-{orchestrator.step_count}",
                role="tool",
                requestor="assistant",
                content="Error: deterministic fixture",
                error=True,
            )
        )
        orchestrator.step_count += 1
        if orchestrator.step_count == 2:
            orchestrator.done = True
            orchestrator.termination_reason = TerminationReason.USER_STOP

    monkeypatch.setattr(orchestrator, "initialize", fake_initialize)
    monkeypatch.setattr(orchestrator, "step", fake_step)
    monkeypatch.setattr("tau2.orchestrator.orchestrator.get_cost", lambda _: (0.0, 0.0))

    simulation = orchestrator.run()

    assert orchestrator._count_errors(simulation.messages) == 2
    assert orchestrator.num_errors == 0
    assert simulation.termination_reason == TerminationReason.USER_STOP
