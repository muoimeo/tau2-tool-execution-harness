from pathlib import Path
from types import SimpleNamespace

import pytest

from tau2.utils.display import ConsoleDisplay


class _RecordingConsole:
    def __init__(self, encoding: str):
        self.file = SimpleNamespace(encoding=encoding)
        self.items: list[tuple[object, dict]] = []

    def print(self, value, **kwargs):
        text = str(value)
        text.encode(self.file.encoding)
        self.items.append((value, kwargs))


def _simulation():
    reward = SimpleNamespace(
        reward=1.0,
        reward_breakdown={"DB": 1.0, "COMMUNICATE": 1.0},
        db_check=SimpleNamespace(db_match=True, db_reward=1.0),
        communicate_checks=[
            SimpleNamespace(info="polyester", met=True),
            SimpleNamespace(info="cotton", met=False),
        ],
    )
    return SimpleNamespace(
        id="sim-1",
        task_id="71",
        trial=0,
        duration=3.25,
        termination_reason="user_stop",
        agent_cost=0.0,
        user_cost=0.0,
        reward_info=reward,
        messages=[],
    )


@pytest.mark.parametrize("encoding", ["cp1252", "cp437"])
def test_non_utf8_console_uses_ascii_simulation_table_and_keeps_artifacts_unchanged(
    tmp_path: Path, monkeypatch, encoding: str
):
    console = _RecordingConsole(encoding)
    monkeypatch.setattr(ConsoleDisplay, "console", console)
    lifecycle = tmp_path / "episode.lifecycle.json"
    results = tmp_path / "results.json"
    lifecycle.write_bytes(b'{"status":"closed"}\n')
    results.write_bytes(b'{"simulations":[]}\n')
    before = {path: path.read_bytes() for path in (lifecycle, results)}

    assert ConsoleDisplay.unicode_output_supported() is False
    ConsoleDisplay.display_simulation(_simulation(), show_details=False)

    output = "\n".join(str(value) for value, _ in console.items)
    assert output.isascii()
    assert "+" in output and "|" in output
    assert "Task ID: 71" in output
    assert "Trial: 0" in output
    assert "Reward: [PASS]" in output
    assert "DB Check: [PASS]" in output
    assert "Communicate Checks:" in output
    assert "polyester [PASS]" in output
    assert "cotton [FAIL]" in output
    assert {path: path.read_bytes() for path in (lifecycle, results)} == before


def test_utf8_console_keeps_rich_simulation_rendering(monkeypatch):
    console = _RecordingConsole("utf-8")
    monkeypatch.setattr(ConsoleDisplay, "console", console)

    assert ConsoleDisplay.unicode_output_supported() is True
    ConsoleDisplay.display_simulation(_simulation(), show_details=False)

    assert console.items
    assert console.items[0][0].__class__.__name__ == "Panel"


@pytest.mark.parametrize("encoding", ["cp1252", "cp437"])
def test_non_utf8_console_uses_ascii_metrics_table(monkeypatch, encoding: str):
    console = _RecordingConsole(encoding)
    monkeypatch.setattr(ConsoleDisplay, "console", console)
    metrics = SimpleNamespace(avg_reward=0.5, pass_hat_ks={1: 0.5}, avg_agent_cost=0.0)

    ConsoleDisplay.display_agent_metrics(metrics)

    output = "\n".join(str(value) for value, _ in console.items)
    assert output.isascii()
    assert "Agent Metrics" in output
    assert "Average Reward: 0.5000" in output
    assert "Pass^1: 0.500" in output
