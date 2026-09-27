from pathlib import Path
from typing import Callable

import pytest

from tau2.data_model.tasks import Task
from tau2.environment.environment import Environment
from tau2.registry import registry
from tau2.run import get_tasks


# These legacy tests intentionally exercise real LLM/provider paths.  They are
# excluded from the offline gate, rather than silently permitted to use local
# credentials or a configured gateway.
_LIVE_GATEWAY_TESTS = {
    "tests/test_llm_utils.py",
    "tests/test_agent.py::test_agent",
    "tests/test_agent.py::test_solo_agent",
    "tests/test_user.py::test_user_simulator",
    "tests/test_orchestrator.py::test_orchestrator_step",
    "tests/test_orchestrator.py::test_orchestrator_restart",
    "tests/test_orchestrator.py::test_orchestrator_run",
    "tests/test_orchestrator.py::test_orchestrator_run_with_solo_agent",
    "tests/test_run.py::test_simplified_run",
    "tests/test_run.py::test_run_tasks_base",
    "tests/test_run.py::test_run_task_base",
    "tests/test_run.py::test_run_tasks_message_history",
    "tests/test_run.py::test_run_tasks_initialization_data",
    "tests/test_run.py::test_run_tasks_initialization_actions",
    "tests/test_run.py::test_run_tasks_env_assertions",
    "tests/test_run.py::test_run_tasks_history_and_env_assertions",
    "tests/test_run.py::test_run_tasks_nl_assertions",
    "tests/test_run.py::test_run_tasks_action_checks",
    "tests/test_run.py::test_run_domain",
    "tests/test_run.py::test_run_gt_agent",
    "tests/test_run.py::test_run_solo_agent",
}


def pytest_collection_modifyitems(config, items):
    """Classify every collected test item into the frozen execution classes."""
    root = Path(config.rootpath).resolve()
    for item in items:
        path = Path(str(item.fspath)).resolve().relative_to(root).as_posix()
        key = path if path == "tests/test_llm_utils.py" else f"{path}::{item.originalname}"
        if key in _LIVE_GATEWAY_TESTS:
            item.add_marker(pytest.mark.live_gateway)
            item.add_marker(pytest.mark.environment_dependent)
        else:
            item.add_marker(pytest.mark.offline_deterministic)


@pytest.fixture(autouse=True)
def _block_real_provider_boundary_in_offline_tests(request, monkeypatch):
    """Fail closed if an offline test reaches the unmocked LiteLLM boundary.

    Offline tests which need a provider response explicitly replace this
    function with their own deterministic stub using ``monkeypatch``.
    """
    if request.node.get_closest_marker("offline_deterministic") is None:
        return

    from tau2.utils import llm_utils
    from tau2.harness import retry as retry_module

    def forbidden_completion(*_args, **_kwargs):
        raise AssertionError(
            "offline_deterministic test reached real LiteLLM completion; "
            "install a deterministic completion stub instead"
        )

    monkeypatch.setattr(llm_utils, "completion", forbidden_completion)
    # Production defaults to time.sleep.  Offline deterministic tests must
    # never wait on the real 1s/2s backoff; individual wiring tests replace
    # this no-op with an assertion spy.
    monkeypatch.setattr(retry_module.time, "sleep", lambda _seconds: None)


@pytest.fixture
def domain_name():
    return "mock"


@pytest.fixture
def get_environment() -> Callable[[], Environment]:
    return registry.get_env_constructor("mock")


@pytest.fixture
def base_task() -> Task:
    return get_tasks("mock", task_ids=["create_task_1"])[0]


@pytest.fixture
def task_with_env_assertions() -> Task:
    return get_tasks("mock", task_ids=["create_task_1_with_env_assertions"])[0]


@pytest.fixture
def task_with_message_history() -> Task:
    return get_tasks("mock", task_ids=["update_task_with_message_history"])[0]


@pytest.fixture
def task_with_initialization_data() -> Task:
    return get_tasks("mock", task_ids=["update_task_with_initialization_data"])[0]


@pytest.fixture
def task_with_initialization_actions() -> Task:
    return get_tasks("mock", task_ids=["update_task_with_initialization_actions"])[0]


@pytest.fixture
def task_with_history_and_env_assertions() -> Task:
    return get_tasks("mock", task_ids=["update_task_with_history_and_env_assertions"])[
        0
    ]


@pytest.fixture
def task_with_action_checks() -> Task:
    return get_tasks("mock", task_ids=["impossible_task_1"])[0]
