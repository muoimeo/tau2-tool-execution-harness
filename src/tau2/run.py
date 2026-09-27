import json
import random
import os
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from tau2.agent.llm_agent import LLMAgent, LLMGTAgent, LLMSoloAgent
from tau2.data_model.simulation import (AgentInfo, Info, Results, RunConfig,
                                        SimulationRun, UserInfo)
from tau2.data_model.tasks import Task
from tau2.environment.environment import Environment, EnvironmentInfo
from tau2.evaluator.evaluator import EvaluationType, evaluate_simulation
from tau2.harness.entrypoint import (
    A1EntrypointError,
    A1RunSettings,
    create_episode_harness,
    default_trace_root,
    finalize_episode_after_results_commit,
    validate_a2_episode_before_results_commit,
    mark_episode_operational_failure,
    latest_episode_identity,
    validate_closed_episode_skip,
)
from tau2.harness.episode import ClosedEpisodeSkip
from tau2.harness.retry import ProviderGenerationError, RetryBudgetExhausted
from tau2.metrics.agent_metrics import compute_metrics
from tau2.orchestrator.orchestrator import Orchestrator
from tau2.registry import RegistryInfo, registry
from tau2.user.user_simulator import DummyUser, get_global_user_sim_guidelines
from tau2.utils.display import ConsoleDisplay, Text
from tau2.utils.pydantic_utils import get_pydantic_hash
from tau2.utils.utils import DATA_DIR, get_commit_hash, get_now, show_dict_diff


def _write_a2_operational_accounting(
    *,
    trace_root: Path,
    planned_logical_episodes: int,
    simulations: list[SimulationRun],
    unresolved_records: list[dict],
) -> None:
    """Write A2-only accounting outside native ``results.json`` schema."""
    lifecycle_groups: dict[tuple[str, int, int], list[dict]] = {}
    failure_counts: dict[str, int] = {}
    for path in trace_root.glob("*.lifecycle.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            identity = value["identity"]
            key = (str(identity["task_id"]), int(identity["trial_index"]), int(identity["trial_seed"]))
            lifecycle_groups.setdefault(key, []).append(value)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    for path in trace_root.glob("*.provider-retry.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                code = row.get("failure_code")
                if code:
                    failure_counts[code] = failure_counts.get(code, 0) + 1
                structure = row.get("empty_response_structure") or {}
                if (
                    code == "PROVIDER_EMPTY_ASSISTANT_MESSAGE"
                    and not structure.get("content_present")
                    and not structure.get("tool_calls_present")
                ):
                    if (structure.get("reasoning_content") or {}).get("present"):
                        subtype = "REASONING_ONLY"
                    elif structure.get("provider_specific_fields_present"):
                        subtype = "OTHER_ALTERNATE_OUTPUT"
                    else:
                        subtype = "TRUE_EMPTY"
                    failure_counts[subtype] = failure_counts.get(subtype, 0) + 1
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue

    safe_recovered = 0
    unsafe_recovered = 0
    for history in lifecycle_groups.values():
        history.sort(key=lambda row: row.get("identity", {}).get("episode_attempt", 0))
        latest = history[-1]
        if latest.get("status") != "closed":
            continue
        if any(row.get("status") == "failed_operational" and not row.get("side_effect_may_have_occurred") for row in history):
            safe_recovered += 1
        if any(row.get("status") == "failed_operational" and row.get("side_effect_may_have_occurred") for row in history):
            unsafe_recovered += 1

    # Terminalization rows are append-only evidence. Rebuild the current
    # accounting view from them and deduplicate by logical tuple so repeated
    # wrapper invocations cannot inflate unresolved counts.
    unresolved_by_key = {
        (str(row["task_id"]), int(row["trial"]), int(row["seed"])): row
        for row in unresolved_records
    }
    safe_audit_path = trace_root / "manual-safe-unresolved-audit.jsonl"
    if safe_audit_path.exists():
        for line in safe_audit_path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            try:
                row = json.loads(line)
                if row.get("outcome") != "safe_failure_budget_terminalized":
                    continue
                key = (str(row["task_id"]), int(row["trial_index"]), int(row["trial_seed"]))
                unresolved_by_key.setdefault(key, {
                    "task_id": key[0],
                    "trial": key[1],
                    "seed": key[2],
                    "latest_attempt": int(row["failed_attempt"]),
                    "reason": "safe_failure_budget_exhausted",
                })
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                # The lifecycle/integrity validation remains authoritative;
                # do not manufacture an unresolved tuple from malformed audit.
                continue

    payload = {
        "schema_version": "a2-operational-accounting/v1",
        "planned_logical_episodes": planned_logical_episodes,
        "benchmark_completed": len({(str(row.task_id), int(row.trial), int(row.seed)) for row in simulations}),
        "operationally_unresolved": [
            unresolved_by_key[key] for key in sorted(unresolved_by_key)
        ],
        "safe_failures_recovered": safe_recovered,
        "unsafe_failures_recovered": unsafe_recovered,
        "provider_failure_subtype_counts": failure_counts,
    }
    path = trace_root / "a2-operational-accounting.json"
    temporary = path.with_suffix(".tmp")
    trace_root.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(json.dumps(payload, indent=2, sort_keys=True).encode("utf-8"))
    os.replace(temporary, path)


def _count_persisted_safe_operational_failures(trace_root: Path) -> int:
    """Count safe A2 failures already recorded for this frozen run root.

    ``run_tasks`` is invoked again by the resume wrapper in a new Python
    process.  A process-local counter would therefore reset on every resume
    and could permit an unbounded sequence of safe failed attempts.  The
    lifecycle artifacts are append-only, so they are the source of truth for
    the frozen A2 operational-failure budget.
    """
    count = 0
    for path in trace_root.glob("*.lifecycle.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            # A corrupt lifecycle is an integrity error elsewhere; it must not
            # be silently treated as a safe failure here.
            continue
        if (
            value.get("status") == "failed_operational"
            and value.get("side_effect_may_have_occurred") is False
        ):
            count += 1
    return count


def _count_safe_failures_for_logical_episode(trace_root: Path, *, task_id: str,
                                              trial_index: int, trial_seed: int) -> int:
    """Count only safe failed attempts belonging to one task/trial/seed."""
    prefix = (f"task-{task_id}__trial-{trial_index:03d}__seed-{trial_seed}"
              "__attempt-")
    count = 0
    for path in trace_root.glob(f"{prefix}*.lifecycle.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
        if (value.get("status") == "failed_operational"
                and value.get("side_effect_may_have_occurred") is False):
            count += 1
    return count


def get_options() -> RegistryInfo:
    """
    Returns options for the simulator.
    """
    return registry.get_info()


def get_environment_info(
    domain_name: str, include_tool_info: bool = False
) -> EnvironmentInfo:
    """Get information about the environment for a registered Domain"""
    global registry
    env_constructor = registry.get_env_constructor(domain_name)
    return env_constructor().get_info(include_tool_info=include_tool_info)


def load_tasks(task_set_name: str) -> list[Task]:
    """
    Loads the tasks for the given domain.
    """
    global registry
    task_loader = registry.get_tasks_loader(task_set_name)
    tasks = task_loader()
    return tasks


def get_tasks(
    task_set_name: str,
    task_ids: Optional[list[str]] = None,
    num_tasks: Optional[int] = None,
) -> list[Task]:
    """
    Loads the tasks for the given domain.
    """
    if task_ids is None:
        tasks = load_tasks(task_set_name=task_set_name)
    else:
        tasks = [
            task for task in load_tasks(task_set_name=task_set_name) if task.id in task_ids
        ]
    if task_ids is not None and len(tasks) != len(task_ids):
        missing_tasks = set(task_ids) - set([task.id for task in tasks])
        raise ValueError(
            f"Not all tasks were found for task set {task_set_name}: {missing_tasks}"
        )
    if num_tasks is not None:
        tasks = tasks[:num_tasks]
    return tasks


def make_run_name(config: RunConfig) -> str:
    """
    Make a run name from the run config
    """
    clean_llm_agent_name = config.llm_agent.split("/")[-1]
    agent_name = f"{config.agent}_{clean_llm_agent_name}"

    clean_llm_user_name = config.llm_user.split("/")[-1]
    user_name = f"{config.user}_{clean_llm_user_name}"

    return f"{get_now()}_{config.domain}_{agent_name}_{user_name}"


def _resolve_harness_trace_root(value: str) -> Path:
    """Resolve explicit A1 traces like ``--save-to`` without changing native paths."""
    path = Path(value)
    return path if path.is_absolute() else DATA_DIR / "simulations" / path


def run_domain(config: RunConfig) -> Results:
    """
    Run simulations for a domain
    """
    config.validate()
    try:
        ConsoleDisplay.display_run_config(config)
    except (UnicodeEncodeError, OSError) as exc:
        logger.warning(f"Non-critical run-config display failed: {type(exc).__name__}")
    if config.task_set_name is None:
        task_set_name = config.domain
    else:
        task_set_name = config.task_set_name
    tasks = get_tasks(task_set_name, config.task_ids, config.num_tasks)
    if "gt" in config.agent:
        total_num_tasks = len(tasks)
        tasks = [task for task in tasks if LLMGTAgent.check_valid_task(task)]
        num_tasks = len(tasks)
        console_text = Text(text=f"Running {num_tasks} out of {total_num_tasks} tasks for GT agent.", style="bold green")
        ConsoleDisplay.safe_print(console_text)
    if "solo" in config.agent:
        total_num_tasks = len(tasks)
        tasks = [task for task in tasks if LLMSoloAgent.check_valid_task(task)]
        num_tasks = len(tasks)
        console_text = Text(text=f"Running {num_tasks} out of {total_num_tasks} tasks for solo agent.", style="bold green")
        ConsoleDisplay.safe_print(console_text)

    num_trials = config.num_trials
    save_to = config.save_to
    if save_to is None:
        save_to = make_run_name(config)
    save_to = DATA_DIR / "simulations" / f"{save_to}.json"
    simulation_results = run_tasks(
        domain=config.domain,
        tasks=tasks,
        agent=config.agent,
        user=config.user,
        llm_agent=config.llm_agent,
        llm_args_agent=config.llm_args_agent,
        llm_user=config.llm_user,
        llm_args_user=config.llm_args_user,
        num_trials=num_trials,
        max_steps=config.max_steps,
        max_errors=config.max_errors,
        save_to=save_to,
        console_display=True,
        evaluation_type=EvaluationType.ALL,
        max_concurrency=config.max_concurrency,
        seed=config.seed,
        log_level=config.log_level,
        harness_enabled=config.harness_enabled,
        harness_trace_root=(
            _resolve_harness_trace_root(config.harness_trace_root)
            if config.harness_trace_root is not None
            else None
        ),
        a2_retry=config.a2_retry,
        a3_recovery=config.a3_recovery,
        a2_max_operational_failures=config.a2_max_operational_failures,
        a2_safe_operational_failure_scope=config.a2_safe_operational_failure_scope,
        a2_max_unsafe_manual_resumes=config.a2_max_unsafe_manual_resumes,
        a2_authorize_unsafe_resume=config.a2_authorize_unsafe_resume,
        a2_authorize_no_write_resume=config.a2_authorize_no_write_resume,
        a2_declare_safe_unresolved=config.a2_declare_safe_unresolved,
        a2_skip_unresolved=config.a2_skip_unresolved,
    )
    metrics = compute_metrics(simulation_results)
    try:
        ConsoleDisplay.display_agent_metrics(metrics)
    except (UnicodeEncodeError, OSError) as exc:
        logger.warning(f"Non-critical metrics display failed: {type(exc).__name__}")

    return simulation_results


def run_tasks(
    domain: str,
    tasks: list[Task],
    agent: str,
    user: str,
    llm_agent: Optional[str] = None,
    llm_args_agent: Optional[dict] = None,
    llm_user: Optional[str] = None,
    llm_args_user: Optional[dict] = None,
    num_trials: int = 1,
    max_steps: int = 100,
    max_errors: int = 10,
    save_to: Optional[str | Path] = None,
    console_display: bool = True,
    evaluation_type: EvaluationType = EvaluationType.ALL,
    max_concurrency: int = 1,
    seed: Optional[int] = 300,
    log_level: Optional[str] = "INFO",
    harness_enabled: bool = False,
    harness_trace_root: Optional[str | Path] = None,
    a2_retry: bool = False,
    a3_recovery: bool = False,
    a2_max_operational_failures: int = 3,
    a2_safe_operational_failure_scope: str = "global_root",
    a2_max_unsafe_manual_resumes: int = 2,
    a2_authorize_unsafe_resume: Optional[list[str]] = None,
    a2_authorize_no_write_resume: Optional[list[str]] = None,
    a2_declare_safe_unresolved: Optional[list[str]] = None,
    a2_skip_unresolved: Optional[list[str]] = None,
    # FI-v1-only, optional observation hook. It is never populated by CLI or
    # normal A0/A1/A2 execution, and only wraps a newly created environment.
    tool_execution_observer_factory: Callable[[Task, int, int], Any] | None = None,
    native_result_fault_injector_factory: Callable[[Task, int, int], Any] | None = None,
) -> Results:
    """
    Runs tasks for a given domain.
    If llm_as_judge is True, the LLM will be used to annotate the simulation run.
    Calculates the reward for the simulation run.
    Args:
        domain (str): The domain to run the simulation on.
        tasks (list[Task]): The tasks to run.
        agent (str): The agent to run the simulation on.
        user (str): The user to run the simulation on.
        llm_agent (str): The model to use for the agent.
        llm_args_agent (dict): The arguments to pass to the LLM for the agent.
        llm_user (str): The model to use for the user.
        llm_args_user (dict): The arguments to pass to the LLM for the user.
        max_steps (int): The maximum number of steps to run the simulation.
        max_errors (int): The maximum number of errors to allow in the simulation.
        save_to (str | Path): The path to json file where to save the simulation results. If the file already exists, it will try to resume the run.
        evaluation_type (EvaluationType): The type of evaluation to use.
        max_concurrency (int): The maximum number of concurrent simulations to run.
        seed (int): The seed to use for the simulation.
        log_level (str): The log level to use.
    Returns:
        The simulation results and the annotations (if llm_review is True).
    """
    if isinstance(save_to, str):
        save_to = Path(save_to)
    # Set log level from config
    logger.remove()
    logger.add(lambda msg: print(msg), level=log_level)
    if len(tasks) == 0:
        raise ValueError("No tasks to run")
    if num_trials <= 0:
        raise ValueError("Number of trials must be greater than 0")
    if max_steps <= 0:
        raise ValueError("Max steps must be greater than 0")
    if max_errors <= 0:
        raise ValueError("Max errors must be greater than 0")
    if harness_enabled and max_concurrency != 1:
        raise A1EntrypointError("A1 v1 requires max_concurrency=1")
    if harness_enabled and agent != "llm_agent":
        raise A1EntrypointError("A1 v1 supports only agent=llm_agent")
    if harness_enabled and save_to is None:
        raise A1EntrypointError("A1 v1 requires --save-to for deterministic artifacts")
    if a2_retry and not harness_enabled:
        raise A1EntrypointError("A2 retry requires --a1-harness")
    if a3_recovery and not (harness_enabled and a2_retry):
        raise A1EntrypointError("A3 recovery requires --a1-harness and --a2-retry")
    if a2_max_operational_failures < 0:
        raise A1EntrypointError("A2 max operational failures must be non-negative")
    if a2_safe_operational_failure_scope not in {"global_root", "per_logical_episode"}:
        raise A1EntrypointError("A2 safe operational-failure scope is invalid")
    if a2_safe_operational_failure_scope != "global_root" and not a2_retry:
        raise A1EntrypointError("A2 safe operational-failure scope requires --a2-retry")
    if (a2_safe_operational_failure_scope == "per_logical_episode"
            and a2_max_operational_failures < 1):
        raise A1EntrypointError("per-logical A2 safe-failure budget must be positive")
    if a2_max_unsafe_manual_resumes < 0:
        raise A1EntrypointError("A2 max unsafe manual resumes must be non-negative")
    if a2_authorize_unsafe_resume and not a2_retry:
        raise A1EntrypointError("unsafe resume authorization requires --a2-retry")
    if a2_authorize_no_write_resume and not a2_retry:
        raise A1EntrypointError("no-WRITE resume authorization requires --a2-retry")
    if a2_declare_safe_unresolved and not a2_retry:
        raise A1EntrypointError("safe unresolved declaration requires --a2-retry")
    if a2_skip_unresolved and not (a2_retry and harness_enabled):
        raise A1EntrypointError("unresolved-episode skip requires A1 harness and A2 retry")

    skip_pairs: set[tuple[str, int]] = set()
    for value in a2_skip_unresolved or []:
        parts = value.split(":")
        if len(parts) != 2 or not parts[0]:
            raise A1EntrypointError("unresolved-episode skip must have the form TASK:TRIAL")
        try:
            trial_value = int(parts[1])
        except ValueError as exc:
            raise A1EntrypointError("unresolved-episode skip trial must be an integer") from exc
        if trial_value < 0 or trial_value >= num_trials:
            raise A1EntrypointError("unresolved-episode skip trial is outside the frozen run")
        skip_pairs.add((parts[0], trial_value))

    random.seed(seed)

    seeds = [random.randint(0, 1000000) for _ in range(num_trials)]
    if "seed" in llm_args_agent:
        logger.warning("Each trial will modify the seed for the agent")

    if "seed" in llm_args_user:
        logger.warning("Each trial will modify the seed for the user")

    lock = multiprocessing.Lock()

    a1_settings = None
    if harness_enabled:
        trace_root = Path(harness_trace_root) if harness_trace_root is not None else default_trace_root(save_to=save_to)
        a1_settings = A1RunSettings.create(
            trace_root=trace_root,
            domain=domain,
            task_ids=[str(task.id) for task in tasks],
            agent=agent,
            user=user,
            llm_agent=llm_agent,
            llm_user=llm_user,
            num_trials=num_trials,
            trial_seeds=seeds,
            max_steps=max_steps,
            max_errors=max_errors,
            max_concurrency=max_concurrency,
            root_seed=seed,
            llm_args_agent=llm_args_agent,
            llm_args_user=llm_args_user,
            a2_retry=a2_retry,
            a3_recovery=a3_recovery,
            a2_max_operational_failures=a2_max_operational_failures,
            a2_safe_operational_failure_scope=a2_safe_operational_failure_scope,
            a2_max_unsafe_manual_resumes=a2_max_unsafe_manual_resumes,
        )
        a1_settings.write_or_validate_config()

    if a2_authorize_unsafe_resume:
        from tau2.harness.integration_contracts import validate_unsafe_resume_authorization
        for authorization in a2_authorize_unsafe_resume:
            validate_unsafe_resume_authorization(
                a1_settings,
                authorization,
                scheduled_task_ids={str(task.id) for task in tasks},
            )
    if a2_authorize_no_write_resume:
        from tau2.harness.integration_contracts import validate_no_write_resume_authorization
        for authorization in a2_authorize_no_write_resume:
            validate_no_write_resume_authorization(
                a1_settings, authorization,
                scheduled_task_ids={str(task.id) for task in tasks},
            )
    if a2_declare_safe_unresolved:
        from tau2.harness.integration_contracts import declare_safe_failure_unresolved
        for declaration in a2_declare_safe_unresolved:
            declared_pair = declare_safe_failure_unresolved(
                a1_settings,
                declaration,
                scheduled_task_ids={str(task.id) for task in tasks},
                max_safe_failures_per_logical_episode=a2_max_operational_failures,
            )
            skip_pairs.add(declared_pair)

    # A completed A1 resume must not instantiate an Environment merely to
    # reconstruct Info. Its frozen A1 sidecar already binds models/args/tasks
    # and the native results contain the original Info. Native (A1-off) resume
    # intentionally retains its existing behavior below.
    if harness_enabled and save_to is not None and save_to.exists():
        previous = Results.load(save_to)
        if not all(
            get_pydantic_hash(task) == get_pydantic_hash(previous_task)
            for task, previous_task in zip(
                sorted(tasks, key=lambda x: x.id),
                sorted(previous.tasks, key=lambda x: x.id),
            )
        ):
            raise A1EntrypointError("A1 resume task set does not match serialized results")
        completed = {
            (simulation.trial, simulation.task_id, simulation.seed)
            for simulation in previous.simulations
        }
        planned = {
            (trial, task.id, seeds[trial])
            for trial in range(num_trials)
            for task in tasks
        }
        if planned.issubset(completed):
            for trial, task_id, trial_seed in planned:
                if not validate_closed_episode_skip(
                    a1_settings,
                    a1_settings.identity(
                        task_id=task_id, trial_index=trial, trial_seed=trial_seed
                    ),
                ):
                    raise A1EntrypointError(
                        "serialized A1 result has no verified closed trace/lifecycle"
                    )
            ConsoleDisplay.safe_print("All A1 episodes already have verified closed traces; nothing to run.")
            return previous

    info = get_info(
        domain=domain,
        agent=agent,
        user=user,
        llm_agent=llm_agent,
        llm_args_agent=llm_args_agent,
        llm_user=llm_user,
        llm_args_user=llm_args_user,
        num_trials=num_trials,
        max_steps=max_steps,
        max_errors=max_errors,
        seed=seed,
    )
    simulation_results = Results(
        info=info,
        tasks=tasks,
        simulations=[],
    )
    done_runs = set()
    if save_to is not None:
        # If save_to already exists, check if the user wants to resume the run.
        if save_to.exists():
            response = (
                ConsoleDisplay.console.input(
                    "[yellow]File [bold]{}[/bold] already exists. Do you want to resume the run? (y/n)[/yellow] ".format(
                        save_to
                    )
                )
                .lower()
                .strip()
            )
            if response != "y":
                raise FileExistsError(
                    f"File {save_to} already exists. Please delete it or use a different save_to name."
                )
            with open(save_to, "r") as fp:
                prev_simulation_results = Results.model_validate_json(fp.read())
                # Check if the run config has changed
                if get_pydantic_hash(prev_simulation_results.info) != get_pydantic_hash(
                    simulation_results.info
                ):
                    diff = show_dict_diff(
                        prev_simulation_results.info.model_dump(),
                        simulation_results.info.model_dump(),
                    )
                    ConsoleDisplay.safe_print(
                        f"The run config has changed.\n\n{diff}\n\nDo you want to resume the run? (y/n)"
                    )
                    response = (
                        ConsoleDisplay.console.input(
                            "[yellow]File [bold]{}[/bold] already exists. Do you want to resume the run? (y/n)[/yellow] ".format(
                                save_to
                            )
                        )
                        .lower()
                        .strip()
                    )
                    if response != "y":
                        raise ValueError(
                            "The run config has changed. Please delete the existing file or use a different save_to name."
                        )
                # Check if the task set has changed
                if not all(
                    get_pydantic_hash(task) == get_pydantic_hash(prev_task)
                    for task, prev_task in zip(
                        sorted(simulation_results.tasks, key=lambda x: x.id),
                        sorted(prev_simulation_results.tasks, key=lambda x: x.id),
                    )
                ):
                    raise ValueError(
                        "The task set has changed. Please delete the existing file or use a different save_to name."
                    )
                # Check which of the runs have already been done
                done_runs = set(
                    [
                        (sim.trial, sim.task_id, sim.seed)
                        for sim in prev_simulation_results.simulations
                    ]
                )
                simulation_results = prev_simulation_results
                console_text = Text(text=f"Resuming run from {len(done_runs)} runs. {len(tasks) * num_trials - len(done_runs)} runs remaining.", style="bold yellow")
                ConsoleDisplay.safe_print(console_text)
        # Create new save file
        else:
            # Check if save_to exists and create parent directories if needed
            if not save_to.parent.exists():
                save_to.parent.mkdir(parents=True, exist_ok=True)
            logger.info(f"Saving simulation batch to {save_to}")
            with open(save_to, "w") as fp:
                fp.write(simulation_results.model_dump_json(indent=2))

    def _save(simulation: SimulationRun):
        if save_to is None:
            return
        with lock:
            with open(save_to, "r") as fp:
                ckpt = json.load(fp)
            ckpt["simulations"].append(simulation.model_dump())
            with open(save_to, "w") as fp:
                json.dump(ckpt, fp, indent=2)

    # V3 freezes the scope in its config.  The older amendment remains
    # readable for historical roots, but only an explicit v3 scope changes
    # scheduling for a newly created root.
    legacy_safe_failure_amendment_active = (
        a2_retry
        and a1_settings is not None
        and (a1_settings.trace_root / "a2-safe-failure-protocol-amendment-v1.json").exists()
    )
    safe_failure_per_logical_episode = (
        a2_retry
        and a1_settings is not None
        and a1_settings.config_payload.get("safe_operational_failure_scope")
        == "per_logical_episode"
    )
    # The historical amendment still selects per-tuple counting, but requires
    # its old explicit declaration. Only the frozen v3 config enables
    # automatic/idempotent terminalization.
    safe_failure_per_tuple_policy = (
        safe_failure_per_logical_episode or legacy_safe_failure_amendment_active
    )
    operational_failures = (
        _count_persisted_safe_operational_failures(a1_settings.trace_root)
        if a2_retry and a1_settings is not None
        and not safe_failure_per_tuple_policy
        else 0
    )

    def _has_completed_write(identity) -> bool:
        """Conservatively detect a WRITE that may have committed in this attempt."""
        latest_identity = latest_episode_identity(a1_settings, identity)
        trace_path = a1_settings.trace_root / f"{latest_identity.filename_stem()}.jsonl"
        if not trace_path.exists():
            return False
        for line in trace_path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A malformed trace is an integrity problem, never safe to continue.
                return True
            if event.get("phase") == "dispatch_completed" and event.get("native_tool_type") == "write":
                return True
        return False

    def _run(task: Task, trial: int, seed: int, progress_str: str) -> SimulationRun | None:
        nonlocal operational_failures
        # Preserve the legacy frozen "stop when exceeded" semantics. If a previous
        # invocation already exceeded the budget, do not instantiate another
        # Environment or make another provider call.
        if not safe_failure_per_tuple_policy and operational_failures > a2_max_operational_failures:
            raise A1EntrypointError(
                "A2 safe operational-failure budget already exceeded across "
                f"the frozen run root: {operational_failures}>"
                f"{a2_max_operational_failures}"
            )
        if safe_failure_per_tuple_policy:
            safe_failures_for_tuple = _count_safe_failures_for_logical_episode(
                a1_settings.trace_root, task_id=str(task.id), trial_index=trial, trial_seed=seed,
            )
            if safe_failures_for_tuple >= a2_max_operational_failures:
                raise A1EntrypointError(
                    "A2 per-logical-episode safe-failure budget exhausted before scheduling; "
                    "the supervisor must terminalize this tuple"
                )
        console_text = Text(text=f"{progress_str}. Running task {task.id}, trial {trial + 1}", style="bold green")
        ConsoleDisplay.safe_print(console_text)
        try:
            run_task_kwargs = dict(
                domain=domain,
                task=task,
                agent=agent,
                user=user,
                llm_agent=llm_agent,
                llm_args_agent=llm_args_agent,
                llm_user=llm_user,
                llm_args_user=llm_args_user,
                max_steps=max_steps,
                max_errors=max_errors,
                evaluation_type=evaluation_type,
                seed=seed,
                trial_index=trial,
                a1_settings=a1_settings,
                a2_retry=a2_retry,
                a3_recovery=a3_recovery,
                unsafe_resume_authorization=(
                    next((value for value in (a2_authorize_unsafe_resume or [])
                          if value.split(":", 2)[0] == str(task.id)
                          and value.split(":", 2)[1] == str(trial)), None)
                ),
                no_write_resume_authorization=(
                    next((value for value in (a2_authorize_no_write_resume or [])
                          if value.split(":", 2)[0] == str(task.id)
                          and value.split(":", 2)[1] == str(trial)), None)
                ),
            )
            if tool_execution_observer_factory is not None:
                run_task_kwargs["tool_execution_observer"] = tool_execution_observer_factory(task, trial, seed)
            if native_result_fault_injector_factory is not None:
                run_task_kwargs["native_result_fault_injector"] = native_result_fault_injector_factory(task, trial, seed)
            simulation = run_task(**run_task_kwargs)
            simulation.trial = trial
            if a1_settings is not None:
                validate_a2_episode_before_results_commit(a1_settings, a1_settings.identity(task_id=task.id, trial_index=trial, trial_seed=seed))
            _save(simulation)
            if a1_settings is not None:
                finalize_episode_after_results_commit(
                    a1_settings,
                    a1_settings.identity(
                        task_id=task.id, trial_index=trial, trial_seed=seed
                    ),
                )
            if console_display:
                ConsoleDisplay.display_simulation_best_effort(
                    simulation, show_details=False
                )
        except RetryBudgetExhausted as e:
            identity = a1_settings.identity(task_id=task.id, trial_index=trial, trial_seed=seed) if a1_settings is not None else None
            unsafe = identity is None or _has_completed_write(identity)
            if a1_settings is not None and identity is not None:
                mark_episode_operational_failure(
                    a1_settings, identity, e, side_effect_may_have_occurred=unsafe,
                )
                if unsafe:
                    from tau2.harness.provider_observability import write_unsafe_provider_audit_packet
                    try:
                        write_unsafe_provider_audit_packet(
                            a1_settings.trace_root,
                            latest_episode_identity(a1_settings, identity),
                            failure_code=e.last_error.kind.value,
                            domain=domain,
                        )
                    except Exception as audit_error:
                        # Observability must not replace the original unsafe
                        # RetryBudgetExhausted or weaken its fail-fast policy.
                        logger.error(f"Unsafe provider audit packet failed: {audit_error}")
            logger.error(f"A2 provider retry exhausted for task {task.id}, trial {trial}; unsafe={unsafe}")
            if unsafe:
                raise
            operational_failures += 1
            if (not safe_failure_per_tuple_policy
                    and operational_failures > a2_max_operational_failures):
                raise A1EntrypointError(
                    "A2 safe operational-failure budget exceeded: "
                    f"{operational_failures}>{a2_max_operational_failures}"
                ) from e
            return None
        except Exception as e:
            if a1_settings is not None:
                side_effect = _has_completed_write(
                    a1_settings.identity(task_id=task.id, trial_index=trial, trial_seed=seed)
                ) if a2_retry else True
                mark_episode_operational_failure(
                    a1_settings,
                    a1_settings.identity(
                        task_id=task.id, trial_index=trial, trial_seed=seed
                    ),
                    e,
                    side_effect_may_have_occurred=side_effect,
                )
                # A structured or generic provider failure before any WRITE
                # is operationally safe: retain its lifecycle evidence, do
                # not serialize a fake reward, and let the A2 batch budget
                # decide whether later logical episodes may continue.
                # Only a typed provider-generation failure may be contained
                # here. Integrity/configuration/evaluator exceptions must
                # still fail fast even if no WRITE appears in the trace.
                if a2_retry and isinstance(e, ProviderGenerationError) and not side_effect:
                    operational_failures += 1
                    if (not safe_failure_per_tuple_policy
                            and operational_failures > a2_max_operational_failures):
                        raise A1EntrypointError(
                            "A2 safe operational-failure budget exceeded: "
                            f"{operational_failures}>{a2_max_operational_failures}"
                        ) from e
                    logger.error(
                        f"Safe operational failure for task {task.id}, trial {trial}: {e}"
                    )
                    return None
            logger.error(f"Error running task {task.id}, trial {trial}: {e}")
            raise e
        return simulation

    unresolved_keys: set[tuple[int, str, int]] = set()
    unresolved_records: list[dict] = []
    if skip_pairs:
        from tau2.harness.integration_contracts import validate_a2_unresolved_episode_skip
        scheduled_ids = {str(task.id) for task in tasks}
        for task_id, trial_value in sorted(skip_pairs):
            if task_id not in scheduled_ids:
                raise A1EntrypointError(
                    f"unresolved-episode skip names an unscheduled task: {task_id}"
                )
            seed_value = seeds[trial_value]
            if (trial_value, task_id, seed_value) in done_runs:
                continue
            latest, reason = validate_a2_unresolved_episode_skip(
                a1_settings,
                task_id=task_id,
                trial_index=trial_value,
                trial_seed=seed_value,
                max_unsafe_manual_resumes=a2_max_unsafe_manual_resumes,
            )
            unresolved_keys.add((trial_value, task_id, seed_value))
            unresolved_records.append({
                "task_id": task_id,
                "trial": trial_value,
                "seed": seed_value,
                "latest_attempt": latest.identity.episode_attempt,
                "reason": reason,
            })

    # A3 v3 supervisor policy: a safe failure budget belongs to one logical
    # tuple. Once its immutable failed_operational lifecycle count reaches the
    # frozen limit, terminalize it exactly once and continue every sibling
    # tuple.  This happens before ``args`` is built, so it cannot create an
    # attempt-004, an Environment, or any provider/tool invocation.
    if safe_failure_per_logical_episode:
        from tau2.harness.integration_contracts import terminalize_safe_failure_budget

        for trial_value in range(num_trials):
            seed_value = seeds[trial_value]
            for task in tasks:
                task_id = str(task.id)
                key = (trial_value, task_id, seed_value)
                if key in done_runs or key in unresolved_keys:
                    continue
                safe_count = _count_safe_failures_for_logical_episode(
                    a1_settings.trace_root,
                    task_id=task_id,
                    trial_index=trial_value,
                    trial_seed=seed_value,
                )
                if safe_count < a2_max_operational_failures:
                    continue
                row = terminalize_safe_failure_budget(
                    a1_settings,
                    task_id=task_id,
                    trial_index=trial_value,
                    trial_seed=seed_value,
                    max_safe_failures_per_logical_episode=a2_max_operational_failures,
                )
                unresolved_keys.add(key)
                unresolved_records.append({
                    "task_id": task_id,
                    "trial": trial_value,
                    "seed": seed_value,
                    "latest_attempt": row["failed_attempt"],
                    "reason": "safe_failure_budget_exhausted",
                })

    args = []
    for trial in range(num_trials):
        for i, task in enumerate(tasks):
            if (trial, task.id, seeds[trial]) in done_runs:
                if a1_settings is not None:
                    identity = a1_settings.identity(
                        task_id=task.id, trial_index=trial, trial_seed=seeds[trial]
                    )
                    if not validate_closed_episode_skip(a1_settings, identity):
                        raise A1EntrypointError(
                            "native results contain an episode without a closed A1 trace/lifecycle"
                        )
                console_text = Text(text=f"Skipping task {task.id}, trial {trial} because it has already been run.", style="bold yellow")
                ConsoleDisplay.safe_print(console_text)
                continue
            if (trial, str(task.id), seeds[trial]) in unresolved_keys:
                ConsoleDisplay.safe_print(
                    f"Skipping unresolved task {task.id}, trial {trial}; terminal operational policy applies."
                )
                continue
            progress_str = f"{i}/{len(tasks)} (trial {trial + 1}/{num_trials})"
            args.append((task, trial, seeds[trial], progress_str))

    if harness_enabled and not args:
        ConsoleDisplay.safe_print("No runnable A1 episodes remain; serialized and unresolved accounting is unchanged.")
        if a1_settings is not None:
            _write_a2_operational_accounting(
                trace_root=a1_settings.trace_root,
                planned_logical_episodes=len(tasks) * num_trials,
                simulations=simulation_results.simulations,
                unresolved_records=unresolved_records,
            )
        return simulation_results

    with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
        res = list(executor.map(_run, *zip(*args)))
        simulation_results.simulations.extend(simulation for simulation in res if simulation is not None)
    if a1_settings is not None:
        _write_a2_operational_accounting(
            trace_root=a1_settings.trace_root,
            planned_logical_episodes=len(tasks) * num_trials,
            simulations=simulation_results.simulations,
            unresolved_records=unresolved_records,
        )
    ConsoleDisplay.safe_print(
        "\n✨ [bold green]Successfully completed all simulations![/bold green]\n"
        "To review the simulations, run: [bold blue]tau2 view[/bold blue]"
    )
    return simulation_results


def run_task(
    domain: str,
    task: Task,
    agent: str,
    user: str,
    llm_agent: Optional[str] = None,
    llm_args_agent: Optional[dict] = None,
    llm_user: Optional[str] = None,
    llm_args_user: Optional[dict] = None,
    max_steps: int = 100,
    max_errors: int = 10,
    evaluation_type: EvaluationType = EvaluationType.ALL,
    seed: Optional[int] = None,
    trial_index: int = 0,
    a1_settings: Optional[A1RunSettings] = None,
    a2_retry: bool = False,
    a3_recovery: bool = False,
    unsafe_resume_authorization: str | None = None,
    no_write_resume_authorization: str | None = None,
    tool_execution_observer: Any | None = None,
    native_result_fault_injector: Any | None = None,
) -> SimulationRun:
    """
    Runs tasks for a given domain.
     If llm_as_judge is True, the LLM will be used to annotate the simulation run.
     Calculates the reward for the simulation run.
     Args:
         domain (str): The domain to run the simulation on.
         task (Task): The task to run.
         agent (str): The agent to run the simulation on.
         user (str): The user to run the simulation on.
         llm_agent (str): The model to use for the agent.
         llm_args_agent (dict): The arguments to pass to the LLM for the agent.
         llm_user (str): The model to use for the user.
         llm_args_user (dict): The arguments to pass to the LLM for the user.
         max_steps (int): The maximum number of steps to run the simulation.
         max_errors (int): The maximum number of errors to allow in the simulation.
         evaluation_type (EvaluationType): The type of evaluation to use.
         seed (int): The seed to use for the simulation.
     Returns:
         The simulation run.
    """

    if max_steps <= 0:
        raise ValueError("Max steps must be greater than 0")
    if max_errors <= 0:
        raise ValueError("Max errors must be greater than 0")
    global registry
    logger.info(
        f"STARTING SIMULATION: Domain: {domain}, Task: {task.id}, Agent: {agent}, User: {user}"
    )
    environment_constructor = registry.get_env_constructor(domain)
    harness = None
    if a1_settings is not None:
        if seed is None:
            raise A1EntrypointError("A1 v1 requires a concrete trial seed")
        created = create_episode_harness(
            settings=a1_settings,
            task_id=task.id,
            trial_index=trial_index,
            trial_seed=seed,
            environment_factory=environment_constructor,
            unsafe_resume_authorization=unsafe_resume_authorization,
            no_write_resume_authorization=no_write_resume_authorization,
            a3_recovery=a3_recovery,
        )
        if isinstance(created, ClosedEpisodeSkip):
            raise A1EntrypointError(
                "closed A1 episode was scheduled for execution; validate native results resume state"
            )
        harness = created
        environment = harness.environment
    else:
        environment = environment_constructor()
    if tool_execution_observer is not None:
        bind = getattr(tool_execution_observer, "bind", None)
        if not callable(bind):
            raise TypeError("tool_execution_observer must provide bind(environment)")
        bind(environment)
    if native_result_fault_injector is not None:
        bind = getattr(native_result_fault_injector, "bind", None)
        if not callable(bind):
            raise TypeError("native_result_fault_injector must provide bind(environment)")
        bind(environment)
    AgentConstructor = registry.get_agent_constructor(agent)

    solo_mode = False
    if issubclass(AgentConstructor, LLMAgent):
        agent = AgentConstructor(
            tools=environment.get_tools(),
            domain_policy=environment.get_policy(),
            llm=llm_agent,
            llm_args=llm_args_agent,
        )
    elif issubclass(AgentConstructor, LLMGTAgent):
        agent = AgentConstructor(
            tools=environment.get_tools(),
            domain_policy=environment.get_policy(),
            llm=llm_agent,
            llm_args=llm_args_agent,
            task=task,
        )
    elif issubclass(AgentConstructor, LLMSoloAgent):
        solo_mode = True
        environment: Environment = environment_constructor(solo_mode=True)
        user_tools = environment.get_user_tools() if environment.user_tools else []
        agent = AgentConstructor(
            tools=environment.get_tools() + user_tools,
            domain_policy=environment.get_policy(),
            llm=llm_agent,
            llm_args=llm_args_agent,
            task=task,
        )
    else:
        raise ValueError(
            f"Unknown agent type: {AgentConstructor}. Should be LLMAgent or LLMSoloAgent"
        )
    try:
        user_tools = environment.get_user_tools()
    except Exception:
        user_tools = None

    UserConstructor = registry.get_user_constructor(user)
    if issubclass(UserConstructor, DummyUser):
        assert isinstance(agent, LLMSoloAgent), (
            "Dummy user can only be used with solo agent"
        )

    user = UserConstructor(
        tools=user_tools,
        instructions=str(task.user_scenario),
        llm=llm_user,
        llm_args=llm_args_user,
    )
    if a2_retry:
        if harness is None or not isinstance(agent, LLMAgent):
            raise A1EntrypointError("A2 retry requires an LLMAgent episode harness")
        from tau2.harness.retry import ProviderGenerationRetryController, ProviderRetrySidecar
        generation = [0]
        harness.lifecycle_store.provider_retry_path.touch(exist_ok=False)
        def retry_controller(context):
            generation[0] += 1
            sidecar = ProviderRetrySidecar(harness.lifecycle_store.provider_retry_path, generation_id=f"{harness.identity.filename_stem()}-g{generation[0]:04d}", turn_index=context.turn_index, episode_attempt=harness.identity.episode_attempt)
            return ProviderGenerationRetryController(
                observer=sidecar.record,
                diagnostic_context={
                    "task_id": str(harness.identity.task_id),
                    "trial": harness.identity.trial_index,
                    "generation_id": sidecar.generation_id,
                    "turn_index": sidecar.turn_index,
                    "episode_attempt": sidecar.episode_attempt,
                },
            )
        agent.enable_a2_provider_retry(retry_controller)

    orchestrator = Orchestrator(
        domain=domain,
        agent=agent,
        user=user,
        environment=environment,
        task=task,
        max_steps=max_steps,
        max_errors=max_errors,
        seed=seed,
        solo_mode=solo_mode,
        harness=harness,
    )
    try:
        simulation = orchestrator.run()

        reward_info = evaluate_simulation(
            domain=domain,
            task=task,
            simulation=simulation,
            evaluation_type=evaluation_type,
            solo_mode=solo_mode,
        )
    except BaseException as exc:
        # The batch boundary owns A2 exhaustion containment because only it
        # can apply the frozen operational budget and inspect the complete
        # episode trace for a completed WRITE.  Other failures remain
        # conservative here: evaluation may follow a committed mutation.
        if harness is not None and not isinstance(exc, RetryBudgetExhausted):
            mark_episode_operational_failure(
                a1_settings,
                harness.identity,
                exc,
                # An evaluator failure happens after the orchestrated episode;
                # a WRITE may already have committed.
                side_effect_may_have_occurred=True,
            )
        raise

    simulation.reward_info = reward_info

    logger.info(
        f"FINISHED SIMULATION: Domain: {domain}, Task: {task.id}, Agent: {agent.__class__.__name__}, User: {user.__class__.__name__}. Reward: {reward_info.reward}"
    )
    return simulation


def get_info(
    domain: str,
    agent: str,
    user: str,
    llm_agent: Optional[str] = None,
    llm_args_agent: Optional[dict] = None,
    llm_user: Optional[str] = None,
    llm_args_user: Optional[dict] = None,
    num_trials: int = 1,
    max_steps: int = 100,
    max_errors: int = 10,
    seed: Optional[int] = None,
) -> Info:
    user_info = UserInfo(
        implementation=user,
        llm=llm_user,
        llm_args=llm_args_user,
        global_simulation_guidelines=get_global_user_sim_guidelines(),
    )
    agent_info = AgentInfo(
        implementation=agent,
        llm=llm_agent,
        llm_args=llm_args_agent,
    )
    environment_info = get_environment_info(
        domain, include_tool_info=False
    )  # NOTE: Not saving tool info to avoid clutter.
    return Info(
        git_commit=get_commit_hash(),
        num_trials=num_trials,
        max_steps=max_steps,
        max_errors=max_errors,
        user_info=user_info,
        agent_info=agent_info,
        environment_info=environment_info,
        seed=seed,
    )
