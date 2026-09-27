"""Compatibility alias for :mod:`tau2.harness.a1.scoped_parser`."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("tau2.harness.a1.scoped_parser")
