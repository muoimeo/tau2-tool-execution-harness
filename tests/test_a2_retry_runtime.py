"""Offline REC-02B/EXP-04 generation-boundary tests; no gateway."""
from types import SimpleNamespace
import pytest
from tau2.data_model.message import UserMessage
from tau2.harness.retry import ProviderGenerationRetryController, RetryBudgetExhausted
from tau2.harness.retry import ProviderRetrySidecar, RetryAttempt, ProviderFailureKind
from tau2.utils import llm_utils

def response(content=None, tool_calls=None):
    choice = SimpleNamespace(finish_reason="stop", message=SimpleNamespace(role="assistant", content=content, tool_calls=tool_calls))
    choice.to_dict = lambda: {}
    return SimpleNamespace(choices=[choice], model="mock", to_dict=lambda: {})

def test_empty_then_success_has_two_provider_invocations_and_no_internal_retry(monkeypatch):
    calls, seen = [], []
    def fake_completion(**kwargs):
        seen.append(kwargs["num_retries"]); calls.append(1)
        return response() if len(calls) == 1 else response("ok")
    monkeypatch.setattr(llm_utils, "completion", fake_completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    msg = llm_utils.generate("mock", [UserMessage(role="user", content="x")], provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None))
    assert msg.content == "ok" and len(calls) == 2 and seen == [0, 0]

def test_budget_exhaustion_happens_before_assistant_message(monkeypatch):
    calls = []
    monkeypatch.setattr(llm_utils, "completion", lambda **kwargs: (calls.append(kwargs), response())[1])
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate("mock", [UserMessage(role="user", content="x")], provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None))
    assert len(calls) == 3 and all(item["num_retries"] == 0 for item in calls)


def test_litellm_timeout_retries_three_times_with_internal_retry_disabled(monkeypatch):
    from litellm.exceptions import Timeout as LiteLLMTimeout

    calls = []

    def timeout_completion(**kwargs):
        calls.append(kwargs)
        raise LiteLLMTimeout("504 gateway timeout", "mock", "openai")

    monkeypatch.setattr(llm_utils, "completion", timeout_completion)
    with pytest.raises(RetryBudgetExhausted) as captured:
        llm_utils.generate(
            "mock",
            [UserMessage(role="user", content="x")],
            provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
        )
    assert captured.value.last_error.kind == ProviderFailureKind.TIMEOUT
    assert len(calls) == 3
    assert [call["num_retries"] for call in calls] == [0, 0, 0]


def test_litellm_timeout_then_success_recovers_without_constructing_empty_message(monkeypatch):
    from litellm.exceptions import Timeout as LiteLLMTimeout

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise LiteLLMTimeout("504 gateway timeout", "mock", "openai")
        return response("ok")

    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    message = llm_utils.generate(
        "mock",
        [UserMessage(role="user", content="x")],
        provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
    )
    assert message.content == "ok"
    assert len(calls) == 2
    assert [call["num_retries"] for call in calls] == [0, 0]


def test_litellm_rate_limit_then_success_uses_a2_retry_without_empty_message(monkeypatch):
    from litellm.exceptions import RateLimitError

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise RateLimitError("quota exhausted", "openai", "claude-sonnet-5")
        return response("ok")

    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    message = llm_utils.generate(
        "mock",
        [UserMessage(role="user", content="x")],
        provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
    )
    assert message.content == "ok"
    assert len(calls) == 2
    assert [call["num_retries"] for call in calls] == [0, 0]


def test_structured_http_502_then_success_uses_a2_retry_without_empty_message(monkeypatch):
    class StructuredGatewayError(Exception):
        status_code = 502

    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise StructuredGatewayError()
        return response("ok")

    monkeypatch.setattr(llm_utils, "completion", completion)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    message = llm_utils.generate(
        "mock",
        [UserMessage(role="user", content="x")],
        provider_retry_controller=ProviderGenerationRetryController(sleeper=lambda _: None),
    )
    assert message.content == "ok"
    assert len(calls) == 2
    assert [call["num_retries"] for call in calls] == [0, 0]


def test_production_default_sleeper_honors_one_then_two_seconds_and_sidecar_matches(tmp_path, monkeypatch):
    """No injected sleeper in production wiring means real time.sleep is selected."""
    from tau2.harness import retry as retry_module
    import json

    sleeps = []
    monkeypatch.setattr(retry_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(llm_utils, "completion", lambda **_kwargs: response())
    sidecar = ProviderRetrySidecar(tmp_path / "provider.jsonl", generation_id="g1", turn_index=7, episode_attempt=1)
    controller = ProviderGenerationRetryController(observer=sidecar.record)
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate("mock", [UserMessage(role="user", content="private prompt")], provider_retry_controller=controller)
    rows = [json.loads(line) for line in sidecar.path.read_text(encoding="utf-8").splitlines()]
    assert sleeps == [1.0, 2.0]
    assert [row["backoff_seconds"] for row in rows] == [1.0, 2.0, 0.0]
    assert [row["failure_code"] for row in rows] == ["PROVIDER_EMPTY_ASSISTANT_MESSAGE"] * 3
    assert all(row["requested_model"] == "mock" for row in rows)
    assert all(row["message_count"] == 1 and row["tool_schema_count"] == 0 for row in rows)
    raw = sidecar.path.read_text(encoding="utf-8")
    assert "private prompt" not in raw
    assert "messages" not in raw and "tools" not in raw


def test_provider_telemetry_missing_optional_response_metadata_never_crashes(tmp_path, monkeypatch):
    import json

    choice = SimpleNamespace(
        finish_reason="stop", message=SimpleNamespace(role="assistant", content="ok", tool_calls=[])
    )
    choice.to_dict = lambda: {}
    minimal = SimpleNamespace(choices=[choice])
    monkeypatch.setattr(llm_utils, "completion", lambda **_kwargs: minimal)
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0)
    monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    sidecar = ProviderRetrySidecar(tmp_path / "provider.jsonl", generation_id="g1", turn_index=1, episode_attempt=1)
    message = llm_utils.generate(
        "mock", [UserMessage(role="user", content="secret tool args: 123")],
        provider_retry_controller=ProviderGenerationRetryController(
            sleeper=lambda _: None, observer=sidecar.record
        ),
    )
    row = json.loads(sidecar.path.read_text(encoding="utf-8"))
    assert message.content == "ok" and row["outcome"] == "success"
    assert row["resolved_model"] is None and row["request_id"] is None
    assert "secret tool args" not in sidecar.path.read_text(encoding="utf-8")


def test_empty_response_diagnostics_are_metadata_only(monkeypatch):
    warnings = []
    monkeypatch.setattr(llm_utils, "completion", lambda **_kwargs: response())
    monkeypatch.setattr(llm_utils.logger, "warning", lambda *args: warnings.append(args))
    controller = ProviderGenerationRetryController(
        sleeper=lambda _: None,
        diagnostic_context={"generation_id": "g1", "turn_index": 7, "episode_attempt": 1},
    )
    with pytest.raises(RetryBudgetExhausted):
        llm_utils.generate("mock", [UserMessage(role="user", content="secret prompt")], provider_retry_controller=controller)
    rendered = repr(warnings)
    assert "generation_id" in rendered and "provider_attempt" in rendered
    assert "secret prompt" not in rendered

def test_a2_off_preserves_configured_native_retry_argument(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_utils, "completion", lambda **kwargs: (seen.append(kwargs), response("ok"))[1])
    monkeypatch.setattr(llm_utils, "get_response_cost", lambda _: 0.0); monkeypatch.setattr(llm_utils, "get_response_usage", lambda _: None)
    llm_utils.generate("mock", [UserMessage(role="user", content="x")])
    assert seen[0]["num_retries"] == llm_utils.DEFAULT_MAX_RETRIES

def test_provider_sidecar_is_append_only_and_sequences_reset_only_per_file(tmp_path):
    sidecar = ProviderRetrySidecar(tmp_path / "episode.provider-retry.jsonl", generation_id="g1", turn_index=7, episode_attempt=2)
    sidecar.record(RetryAttempt(1, ProviderFailureKind.TIMEOUT, True, 1.0))
    sidecar.record(RetryAttempt(2, None, False, 0.0))
    rows = [__import__("json").loads(x) for x in sidecar.path.read_text().splitlines()]
    assert [r["sequence"] for r in rows] == [1, 2]
    assert [r["provider_attempt"] for r in rows] == [1, 2]

def test_a2_config_changes_frozen_run_identity(tmp_path):
    from tau2.harness.entrypoint import A1RunSettings, A1EntrypointError
    base = dict(trace_root=tmp_path, domain="mock", task_ids=("71",), agent="llm_agent", user="user_simulator", llm_agent="a", llm_user="u", num_trials=1, trial_seeds=(300,), max_steps=2, max_errors=1, max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={})
    a1 = A1RunSettings.create(**base); a1.write_or_validate_config()
    with pytest.raises(A1EntrypointError): A1RunSettings.create(**base, a2_retry=True).write_or_validate_config()


def test_a2_unsafe_resume_budget_is_frozen_run_configuration(tmp_path):
    from tau2.harness.entrypoint import A1RunSettings, A1EntrypointError

    base = dict(trace_root=tmp_path, domain="mock", task_ids=("71",), agent="llm_agent", user="user_simulator", llm_agent="a", llm_user="u", num_trials=1, trial_seeds=(300,), max_steps=2, max_errors=1, max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={}, a2_retry=True)
    settings = A1RunSettings.create(**base, a2_max_unsafe_manual_resumes=2)
    settings.write_or_validate_config()
    assert settings.config_payload["max_unsafe_manual_resumes"] == 2
    assert settings.config_payload["provider_internal_num_retries"] == 0
    assert settings.config_payload["provider_max_attempts"] == 3
    assert settings.config_payload["provider_backoff_seconds"] == [1.0, 2.0]
    assert settings.config_payload["retry_eligible_provider_failures"]["PROVIDER_TIMEOUT"] == [
        "builtins.TimeoutError",
        "litellm.exceptions.Timeout",
    ]
    assert settings.config_payload["retry_eligible_provider_failures"]["PROVIDER_RATE_LIMIT"] == [
        "structured_status_code_429",
        "litellm.exceptions.RateLimitError",
    ]
    with pytest.raises(A1EntrypointError):
        A1RunSettings.create(**base, a2_max_unsafe_manual_resumes=3).write_or_validate_config()


def test_a2_frozen_config_missing_unsafe_resume_budget_fails_validation(tmp_path):
    from tau2.harness.entrypoint import A1RunSettings, A1EntrypointError
    import json

    base = dict(trace_root=tmp_path, domain="mock", task_ids=("71",), agent="llm_agent", user="user_simulator", llm_agent="a", llm_user="u", num_trials=1, trial_seeds=(300,), max_steps=2, max_errors=1, max_concurrency=1, root_seed=300, llm_args_agent={}, llm_args_user={}, a2_retry=True)
    settings = A1RunSettings.create(**base, a2_max_unsafe_manual_resumes=2)
    settings.write_or_validate_config()
    raw = json.loads(settings.config_path.read_text(encoding="utf-8"))
    raw.pop("max_unsafe_manual_resumes")
    settings.config_path.write_text(json.dumps(raw, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    with pytest.raises(A1EntrypointError, match="different frozen run configuration"):
        settings.write_or_validate_config()

def test_clean_and_transient_sidecar_have_terminal_success_events(tmp_path):
    clean = ProviderRetrySidecar(tmp_path / "clean.jsonl", generation_id="g1", turn_index=1, episode_attempt=1)
    clean.record(RetryAttempt(1, None, False, 0.0))
    transient = ProviderRetrySidecar(tmp_path / "transient.jsonl", generation_id="g2", turn_index=2, episode_attempt=1)
    transient.record(RetryAttempt(1, ProviderFailureKind.TIMEOUT, True, 1.0)); transient.record(RetryAttempt(2, None, False, 0.0))
    import json
    assert [json.loads(x)["outcome"] for x in clean.path.read_text().splitlines()] == ["success"]
    assert [json.loads(x)["outcome"] for x in transient.path.read_text().splitlines()] == ["retry_scheduled", "success"]

def test_failed_operational_resume_preserves_attempt_one_and_creates_attempt_two(tmp_path):
    from tau2.harness.integration_contracts import EpisodeIdentity, EpisodeLifecycleStore, LifecycleStatus
    identity = EpisodeIdentity(run_id="a2", task_id="71", trial_index=0, trial_seed=300, episode_attempt=1)
    store = EpisodeLifecycleStore(tmp_path, identity); open_lifecycle = store.open()
    sidecar = ProviderRetrySidecar(store.provider_retry_path, generation_id="task-71__trial-000__seed-300__attempt-001-g0001", turn_index=1, episode_attempt=1)
    for i in range(1, 4): sidecar.record(RetryAttempt(i, ProviderFailureKind.HTTP_503, i < 3, 1.0 if i == 1 else (2.0 if i == 2 else 0.0)))
    failed = store.terminalize(open_lifecycle, status=LifecycleStatus.FAILED_OPERATIONAL, outcome="failed", error="budget exhausted")
    before_lifecycle, before_sidecar = store.lifecycle_path.read_bytes(), store.provider_retry_path.read_bytes()
    next_identity = store.prepare_resume()
    assert next_identity.episode_attempt == 2
    assert store.lifecycle_path.read_bytes() == before_lifecycle and store.provider_retry_path.read_bytes() == before_sidecar
    assert store.read().status == LifecycleStatus.FAILED_OPERATIONAL

def test_corrupt_failed_sidecar_fails_before_attempt_two_creation(tmp_path):
    from tau2.harness.integration_contracts import EpisodeIdentity, EpisodeLifecycleStore, LifecycleStatus
    identity = EpisodeIdentity(run_id="a2", task_id="71", trial_index=0, trial_seed=300, episode_attempt=1)
    store = EpisodeLifecycleStore(tmp_path, identity); open_lifecycle = store.open()
    ProviderRetrySidecar(store.provider_retry_path, generation_id="a1", turn_index=1, episode_attempt=1).record(RetryAttempt(1, ProviderFailureKind.HTTP_503, False, 0.0))
    store.terminalize(open_lifecycle, status=LifecycleStatus.FAILED_OPERATIONAL, outcome="failed", error="x")
    store.provider_retry_path.write_text("corrupt\n")
    with pytest.raises(ValueError, match="snapshot mismatch"):
        store.prepare_resume()
    assert not (tmp_path / "task-71__trial-000__seed-300__attempt-002.lifecycle.json").exists()


def test_unsafe_manual_resume_budget_is_per_task_trial_and_preserves_old_attempts(tmp_path):
    """Two explicit approvals are allowed; the third cannot create attempt four."""
    from tau2.harness.integration_contracts import EpisodeIdentity, EpisodeLifecycleStore, LifecycleStatus

    def failed_store(attempt: int) -> EpisodeLifecycleStore:
        identity = EpisodeIdentity(
            run_id="a2", task_id="23", trial_index=1, trial_seed=373753,
            episode_attempt=attempt,
        )
        store = EpisodeLifecycleStore(tmp_path, identity, max_unsafe_manual_resumes=2)
        opened = store.open()
        store.trace_path.write_text(
            '{"sequence":0,"phase":"dispatch_completed","native_tool_type":"write"}\n',
            encoding="utf-8",
        )
        sidecar = ProviderRetrySidecar(
            store.provider_retry_path,
            generation_id=f"{identity.filename_stem()}-g0001",
            turn_index=5,
            episode_attempt=attempt,
        )
        for provider_attempt in range(1, 4):
            sidecar.record(
                RetryAttempt(
                    provider_attempt,
                    ProviderFailureKind.TIMEOUT,
                    provider_attempt < 3,
                    1.0 if provider_attempt == 1 else (2.0 if provider_attempt == 2 else 0.0),
                )
            )
        store.terminalize(
            opened,
            status=LifecycleStatus.FAILED_OPERATIONAL,
            outcome="failed",
            error="retry exhausted",
            side_effect=True,
        )
        return store

    first = failed_store(1)
    first_lifecycle, first_sidecar = first.lifecycle_path.read_bytes(), first.provider_retry_path.read_bytes()
    assert first.prepare_resume(authorization="23:1:1").episode_attempt == 2
    assert first.lifecycle_path.read_bytes() == first_lifecycle
    assert first.provider_retry_path.read_bytes() == first_sidecar

    second = failed_store(2)
    second_lifecycle, second_sidecar = second.lifecycle_path.read_bytes(), second.provider_retry_path.read_bytes()
    assert second.prepare_resume(authorization="23:1:2").episode_attempt == 3
    assert second.lifecycle_path.read_bytes() == second_lifecycle
    assert second.provider_retry_path.read_bytes() == second_sidecar

    third = failed_store(3)
    third_lifecycle, third_sidecar = third.lifecycle_path.read_bytes(), third.provider_retry_path.read_bytes()
    with pytest.raises(ValueError, match="manual-resume budget exhausted"):
        third.prepare_resume(authorization="23:1:3")
    assert third.lifecycle_path.read_bytes() == third_lifecycle
    assert third.provider_retry_path.read_bytes() == third_sidecar
    assert not (tmp_path / "task-23__trial-001__seed-373753__attempt-004.lifecycle.json").exists()
    assert third.unsafe_resume_authorization_count() == 2
