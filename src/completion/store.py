"""Durable, integrity-protected CompletionDecision persistence.

The latest decision lives at .pi/tasks/<task-id>/completion/decision.json
behind the same SHA-256 integrity envelope as task state and verification
results: atomic write + fsync, digest over canonical compact JSON,
constant-time comparison on load. V-PRINCIPLE-05: the final decision MUST
be persisted and integrity-validated before DONE becomes effective -
tampered decisions fail closed on load (adversarial Test 12).
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

from continuity.task_store import (
    TaskIntegrityError,
    _atomic_write_json,
    _digest,
    _load_envelope,
)
from core import CompletionDecision


class CompletionDecisionError(TaskIntegrityError):
    """Stored completion decision is missing or fails integrity validation."""


class CompletionDecisionStore:
    """Latest CompletionDecision per task."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)

    def _path(self, task_id: str) -> Path:
        return self.root / task_id / "completion" / "decision.json"

    def save(self, decision: CompletionDecision) -> Path:
        payload = decision.to_dict()
        envelope = {"payload": payload,
                    "integrity": {"algorithm": "sha256", "digest": _digest(payload)}}
        path = self._path(decision.taskId)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, envelope)
        return path

    def load(self, task_id: str) -> CompletionDecision:
        path = self._path(task_id)
        if not path.is_file():
            raise CompletionDecisionError(
                f"no stored completion decision for {task_id}")
        return CompletionDecision.from_dict(_load_envelope(path))
