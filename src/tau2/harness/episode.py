"""HAR-07A episode-scoped harness factory; no tau2 runtime hook is installed.

The factory deliberately owns all mutable harness state.  Its caller supplies a
fresh native ``Environment`` constructor for every new episode attempt.  It is
therefore impossible for registry bindings, parser-failure sidecars, duplicate
history, trace state, or a DB-bearing environment instance to be reused across
attempts through this component.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

from pydantic import BaseModel

from tau2.environment.environment import Environment
from tau2.harness.contracts import Requestor
from tau2.harness.dispatcher import (
    A1ValidationExecutor,
    DuplicateDispatchGuard,
    NativeToolExecutor,
)
from tau2.harness.integration_contracts import (
    EpisodeIdentity,
    EpisodeLifecycle,
    EpisodeLifecycleStore,
    OrchestratedHarnessContext,
    ParserFailureSidecar,
)
from tau2.harness.registry import RegistrySnapshot, build_registry_snapshot
from tau2.harness.trace import TraceLogger


ToolBindingKey = tuple[Requestor, str]


def _environment_state_supplier(environment: Environment) -> dict[str, Any]:
    """Return a canonicalizable full state view without mutating native DBs."""
    state: dict[str, Any] = {}
    for name, toolkit in (("assistant", environment.tools), ("user", environment.user_tools)):
        db = getattr(toolkit, "db", None) if toolkit is not None else None
        if db is None:
            continue
        state[name] = db.model_dump(mode="json") if isinstance(db, BaseModel) else db
    return state


@dataclass(frozen=True)
class ClosedEpisodeSkip:
    """A verified completed attempt that must not be re-executed."""

    identity: EpisodeIdentity
    lifecycle: EpisodeLifecycle


@dataclass
class EpisodeHarness:
    """All HAR components and native bindings belonging to one episode attempt."""

    identity: EpisodeIdentity
    environment: Environment
    lifecycle_store: EpisodeLifecycleStore
    lifecycle: EpisodeLifecycle
    registry_snapshot: RegistrySnapshot
    trace_logger: TraceLogger
    duplicate_guard: DuplicateDispatchGuard
    executors: Mapping[ToolBindingKey, A1ValidationExecutor]
    params_models: Mapping[ToolBindingKey, type[BaseModel]]
    parser_rejection_executor: A1ValidationExecutor

    def __post_init__(self) -> None:
        self._sidecars: dict[tuple[int, Requestor], ParserFailureSidecar] = {}

    def context_for_turn(self, *, turn_index: int, requestor: Requestor) -> OrchestratedHarnessContext:
        return OrchestratedHarnessContext(
            run_id=self.identity.run_id,
            task_id=self.identity.task_id,
            trial_index=self.identity.trial_index,
            trial_seed=self.identity.trial_seed,
            turn_index=turn_index,
            requestor=requestor,
        )

    def parser_failure_sidecar(self, *, turn_index: int, requestor: Requestor) -> ParserFailureSidecar:
        key = (turn_index, requestor)
        if key not in self._sidecars:
            self._sidecars[key] = ParserFailureSidecar(
                self.context_for_turn(turn_index=turn_index, requestor=requestor)
            )
        return self._sidecars[key]

    def executor_for(self, *, requestor: Requestor, tool_name: str) -> A1ValidationExecutor:
        try:
            return self.executors[(requestor, tool_name)]
        except KeyError as exc:
            raise KeyError(f"no native callable binding for {requestor.value}/{tool_name}") from exc

    def params_model_for(self, *, requestor: Requestor, tool_name: str) -> type[BaseModel]:
        try:
            return self.params_models[(requestor, tool_name)]
        except KeyError as exc:
            raise KeyError(f"no params model binding for {requestor.value}/{tool_name}") from exc

    def close_resources(self) -> None:
        """Discard every episode-scoped mutable reference after completion."""
        self._sidecars.clear()
        self.duplicate_guard.clear()
        self.trace_logger.close()


class EpisodeHarnessFactory:
    """Create fresh, non-resumable-in-the-middle episode harness attempts."""

    def __init__(self, *, trace_root: Path, environment_factory: Callable[[], Environment]):
        self._trace_root = Path(trace_root)
        self._environment_factory = environment_factory

    @staticmethod
    def _namespaces(environment: Environment):
        if environment.solo_mode:
            raise ValueError("HAR-07A does not support solo-mode binding")
        if environment.tools is None:
            raise ValueError("assistant toolkit is required for HAR-07A")
        namespaces = [(Requestor.ASSISTANT, environment.tools)]
        if environment.user_tools is not None:
            namespaces.append((Requestor.USER, environment.user_tools))
        return namespaces

    @staticmethod
    def _bindings(
        environment: Environment,
        guard: DuplicateDispatchGuard,
    ) -> tuple[Mapping[ToolBindingKey, A1ValidationExecutor], Mapping[ToolBindingKey, type[BaseModel]]]:
        bindings: dict[ToolBindingKey, A1ValidationExecutor] = {}
        params_models: dict[ToolBindingKey, type[BaseModel]] = {}
        supplier = lambda: _environment_state_supplier(environment)
        for requestor, toolkit in EpisodeHarnessFactory._namespaces(environment):
            tool_specs = toolkit.get_tools()
            for tool_name in toolkit.tools:
                def native_callable(*, _tool_name=tool_name, _requestor=requestor, **kwargs):
                    result = environment.make_tool_call(
                        _tool_name, requestor=_requestor.value, **kwargs
                    )
                    environment.sync_tools()
                    return result
                bindings[(requestor, tool_name)] = A1ValidationExecutor(
                    NativeToolExecutor(
                        native_callable,
                        state_supplier=supplier,
                        duplicate_guard=guard,
                    )
                )
                params_models[(requestor, tool_name)] = tool_specs[tool_name].params
        return MappingProxyType(bindings), MappingProxyType(params_models)

    def create(self, identity: EpisodeIdentity) -> EpisodeHarness | ClosedEpisodeSkip:
        store = EpisodeLifecycleStore(self._trace_root, identity)
        if store.lifecycle_path.exists():
            rerun_identity = store.prepare_resume()
            if rerun_identity is None:
                return ClosedEpisodeSkip(identity=identity, lifecycle=store.read())
            identity = rerun_identity
            store = EpisodeLifecycleStore(self._trace_root, identity)
            if store.lifecycle_path.exists():
                raise ValueError("rerun attempt lifecycle already exists")

        # Calling this here, rather than accepting a shared Environment, makes a
        # new attempt start from a clean native environment/DB instance.
        environment = self._environment_factory()
        namespaces = self._namespaces(environment)
        snapshot = build_registry_snapshot(namespaces)
        guard = DuplicateDispatchGuard()
        bindings, params_models = self._bindings(environment, guard)
        rejection_executor = A1ValidationExecutor(
            NativeToolExecutor(lambda **_kwargs: None, state_supplier=lambda: _environment_state_supplier(environment), duplicate_guard=guard)
        )
        lifecycle = store.open()
        return EpisodeHarness(
            identity=identity,
            environment=environment,
            lifecycle_store=store,
            lifecycle=lifecycle,
            registry_snapshot=snapshot,
            trace_logger=TraceLogger(store.trace_path),
            duplicate_guard=guard,
            executors=bindings,
            params_models=params_models,
            parser_rejection_executor=rejection_executor,
        )
