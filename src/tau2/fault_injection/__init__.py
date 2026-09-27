"""Deterministic, local controlled-fault experiments for tau2.

This package is deliberately separate from the A0/A1/A2 runtime.  It supplies
fixtures and a local OpenAI-compatible server for experiments under
``data/simulations/60_fault_injection``; importing it never enables a harness
or changes a normal benchmark run.
"""

from tau2.fault_injection.contracts import FaultKind, FaultScenario

__all__ = ["FaultKind", "FaultScenario"]
