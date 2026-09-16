"""Shared test harness: reuses the Phase 5 verification harness so every
test category exercises the same wiring (store, policy engine, pipeline,
verification engine). The import is name-canonical via importlib so all
suites share one module instance."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

_UNIT_DIR = str(Path(__file__).resolve().parent.parent / "unit")
if _UNIT_DIR not in sys.path:
    sys.path.insert(0, _UNIT_DIR)

_verification_tests = importlib.import_module("test_verification_engine")

VerificationTestBase = _verification_tests.VerificationTestBase
criterion = _verification_tests.criterion
make_tac = _verification_tests.make_tac
