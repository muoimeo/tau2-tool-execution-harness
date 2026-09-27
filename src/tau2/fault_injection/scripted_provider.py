"""A local OpenAI-compatible scripted server used by FI-v1 only.

It deliberately records request *shape* rather than prompts or arguments.  A
LiteLLM call is still made by tau2; this server only replaces the remote API.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from tau2.fault_injection.contracts import CompiledFixture, ProviderAction, ProviderActionKind


@dataclass
class SafeServerRequest:
    ordinal: int
    model: str | None
    path: str
    message_count: int
    tool_schema_count: int
    action_kind: str
    script_ordinal: int
    terminal_fallback: bool


@dataclass
class SafeServerError:
    phase: str
    error_type: str


@dataclass
class LocalScriptedProvider:
    fixture: CompiledFixture
    agent_actions: list[ProviderAction] = field(init=False)
    user_actions: list[ProviderAction] = field(init=False)
    requests: list[SafeServerRequest] = field(default_factory=list)
    errors: list[SafeServerError] = field(default_factory=list)
    _server: ThreadingHTTPServer | None = field(default=None, init=False)
    _thread: threading.Thread | None = field(default=None, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        self.agent_actions = list(self.fixture.agent_actions)
        self.user_actions = list(self.fixture.user_actions)

    @property
    def api_base(self) -> str:
        if self._server is None:
            raise RuntimeError("local scripted provider is not running")
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def _next_action(self, model: str | None) -> tuple[ProviderAction, int, bool]:
        # LiteLLM routes ``openai/foo`` through the OpenAI adapter and sends
        # the transport model ``foo`` in the JSON request.  Both spellings are
        # one frozen alias, not a model-selection fallback.
        agent_transport_name = self.fixture.agent_model.removeprefix("openai/")
        user_transport_name = self.fixture.user_model.removeprefix("openai/")
        if model in {self.fixture.agent_model, agent_transport_name}:
            queue = self.agent_actions
        elif model in {self.fixture.user_model, user_transport_name}:
            queue = self.user_actions
        else:
            raise ValueError(f"FI local provider received unexpected model alias: {model!r}")
        if not queue:
            # A fault changes a conversation's subsequent trajectory. End the
            # fixture explicitly rather than asking a remote model to invent a
            # continuation or turning a proved recovery into script failure.
            # This response carries no tool call and is recorded as a terminal
            # fallback, never mistaken for a source-fixture action.
            return ProviderAction.response_action({"role": "assistant", "content": "###STOP###"}), 0, True
        total = len(self.fixture.agent_actions if model in {self.fixture.agent_model, agent_transport_name} else self.fixture.user_actions)
        return queue.pop(0), total - len(queue), False

    def __enter__(self) -> "LocalScriptedProvider":
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format: str, *_args: Any) -> None:
                return

            def _write_json(self, status: int, body: dict[str, Any]) -> None:
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                try:
                    self.wfile.write(encoded)
                except BrokenPipeError:
                    pass

            def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler contract)
                phase = "read_request"
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    payload = json.loads(self.rfile.read(size).decode("utf-8"))
                    model = payload.get("model")
                    phase = "select_action"
                    action, script_ordinal, terminal_fallback = parent._next_action(model)
                    phase = "record_request"
                    with parent._lock:
                        parent.requests.append(SafeServerRequest(
                            ordinal=len(parent.requests) + 1,
                            model=model,
                            path=self.path,
                            message_count=len(payload.get("messages", [])),
                            tool_schema_count=len(payload.get("tools", []) or []),
                            action_kind=action.kind.value,
                            script_ordinal=script_ordinal,
                            terminal_fallback=terminal_fallback,
                        ))
                    if action.kind == ProviderActionKind.TIMEOUT:
                        time.sleep(action.sleep_seconds or 1.0)
                        return
                    if action.kind == ProviderActionKind.HTTP_ERROR:
                        status = action.http_status or HTTPStatus.INTERNAL_SERVER_ERROR
                        self._write_json(status, {"error": {"message": action.error_type or "fi-error", "type": action.error_type or "fi_error"}})
                        return
                    if action.response is None:
                        raise ValueError("response action missing response payload")
                    phase = "write_response"
                    response = dict(action.response)
                    response.setdefault("id", f"fi-{uuid.uuid4().hex}")
                    response.setdefault("model", model)
                    self._write_json(HTTPStatus.OK, response)
                except Exception as exc:
                    with parent._lock:
                        parent.errors.append(SafeServerError(phase=phase, error_type=type(exc).__name__))
                    self._write_json(HTTPStatus.BAD_REQUEST, {"error": {"message": type(exc).__name__, "type": "fi_script_error"}})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
