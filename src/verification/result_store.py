"""Durable, integrity-protected VerificationResult persistence.

The latest result per criterion lives under
.pi/tasks/<task-id>/verification/<criterion-id>.json behind the same
SHA-256 integrity envelope as task state (CONTINUITY.md, SECURITY.md s21):
atomic write + fsync, digest over canonical compact JSON, constant-time
comparison on load. Tampered or corrupted results fail closed on load
(VERIFICATION.md s6, adversarial Test 11). History lives in the task's
append-only Action Journal; these files are the latest snapshot.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Union

from continuity.task_store import (
    TaskIntegrityError,
    _atomic_write_json,
    _digest,
    _load_envelope,
)
from core import VerificationResult


class VerificationResultError(TaskIntegrityError):
    """Stored verification result is missing or fails integrity validation."""


class VerificationResultStore:
    """Latest VerificationResult per (task, criterion)."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)

    def _dir(self, task_id: str) -> Path:
        return self.root / task_id / "verification"

    def save(self, result: VerificationResult) -> Path:
        payload = result.to_dict()
        envelope = {"payload": payload,
                    "integrity": {"algorithm": "sha256", "digest": _digest(payload)}}
        path = self._dir(result.taskId) / f"{result.criterionId}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, envelope)
        return path

    def load(self, task_id: str, criterion_id: str) -> VerificationResult:
        path = self._dir(task_id) / f"{criterion_id}.json"
        if not path.is_file():
            raise VerificationResultError(
                f"no stored verification result for {task_id}/{criterion_id}")
        return VerificationResult.from_dict(_load_envelope(path))

    def load_all(self, task_id: str) -> Dict[str, VerificationResult]:
        directory = self._dir(task_id)
        if not directory.is_dir():
            return {}
        results: Dict[str, VerificationResult] = {}
        for path in sorted(directory.glob("*.json")):
            result = VerificationResult.from_dict(_load_envelope(path))
            results[result.criterionId] = result
        return results
