"""Append-only, non-sensitive A3 recovery audit sidecar."""

from __future__ import annotations

import os
from pathlib import Path

from tau2.harness.a1.contracts import canonical_json_bytes
from tau2.harness.a3.contracts import RecoveryAuditEvent


class RecoveryAuditTrail:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._sequence = 0
        if self.path.exists():
            self._sequence = len(self.path.read_bytes().splitlines())

    def append(self, event: RecoveryAuditEvent) -> RecoveryAuditEvent:
        if event.sequence != self._sequence + 1:
            raise ValueError("A3 recovery sequence must be contiguous")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("ab") as handle:
            handle.write(canonical_json_bytes(event.model_dump(mode="json")) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._sequence = event.sequence
        return event

    @property
    def next_sequence(self) -> int:
        return self._sequence + 1
