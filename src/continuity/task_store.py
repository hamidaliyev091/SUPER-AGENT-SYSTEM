"""Durable task state store (Phase 2).

The persistence layer the Task Manager uses. Owned conceptually by the
Continuity Manager (full implementation in Phase 7); this Phase 2 version
provides the required minimum:

- structured JSON persistence (CONTINUITY.md s4: JSON is an approved store);
- atomic writes (write temp file, os.replace) so a crash mid-write can never
  leave a half-written authoritative file;
- integrity protection (SECURITY.md s27): every stored payload is wrapped in
  an envelope carrying a SHA-256 digest of the canonical payload JSON.
  Loading fails closed: any tampering, malformed JSON, or schema violation
  raises TaskIntegrityError rather than returning corrupted state
  (CONTINUITY.md s20).

Layout (ADR-003)::

    <root>/
      <task-id>/
        task.json
        checkpoints/
          <checkpoint-id>.json

Envelope format on disk::

    {"payload": <contract dict>, "integrity": {"algorithm": "sha256", "digest": "<hex>"}}

The digest covers the canonical (sorted-key, compact) JSON of `payload`,
identical to the canonicalization used by core contracts (to_json).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import List, Optional, Union

from core import Checkpoint, ContractError, Task, ValidationError


class TaskIntegrityError(ContractError):
    """Authoritative state failed integrity or schema validation (fail closed)."""


class TaskNotFoundError(ContractError):
    """No durable state exists for the given task id."""


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload: dict) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _atomic_write_json(path: Path, obj: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(obj, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _load_envelope(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        raise TaskIntegrityError(f"{path}: unreadable or malformed stored state") from None
    if not isinstance(data, dict):
        raise TaskIntegrityError(f"{path}: stored state is not an object")
    integrity = data.get("integrity")
    payload = data.get("payload")
    if not isinstance(integrity, dict) or not isinstance(payload, dict):
        raise TaskIntegrityError(f"{path}: stored state has no integrity envelope")
    algorithm = integrity.get("algorithm")
    digest = integrity.get("digest")
    if algorithm != "sha256" or not isinstance(digest, str):
        raise TaskIntegrityError(f"{path}: unsupported integrity metadata")
    expected = _digest(payload)
    if not hmac.compare_digest(digest, expected):
        raise TaskIntegrityError(f"{path}: integrity check FAILED (tampered or corrupted)")
    return payload


class TaskStore:
    """Durable, integrity-protected storage for tasks and checkpoints."""

    def __init__(self, root: Union[str, Path] = ".pi/tasks"):
        self.root = Path(root)

    def _task_dir(self, task_id: str) -> Path:
        return self.root / task_id

    def _checkpoints_dir(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "checkpoints"

    # -- tasks ----------------------------------------------------------

    def save_task(self, task: Task) -> Path:
        path = self._task_dir(task.id) / "task.json"
        envelope = {"payload": task.to_dict(), "integrity": {"algorithm": "sha256", "digest": _digest(task.to_dict())}}
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, envelope)
        return path

    def load_task(self, task_id: str) -> Task:
        path = self._task_dir(task_id) / "task.json"
        if not path.is_file():
            raise TaskNotFoundError(f"task {task_id!r} has no durable state")
        payload = _load_envelope(path)
        try:
            return Task.from_dict(payload)
        except ValidationError as exc:
            raise TaskIntegrityError(f"{path}: payload failed schema validation: {exc}") from None

    # -- checkpoints ----------------------------------------------------

    def save_checkpoint(self, checkpoint: Checkpoint) -> Path:
        path = self._checkpoints_dir(checkpoint.taskId) / f"{checkpoint.checkpointId}.json"
        envelope = {"payload": checkpoint.to_dict(),
                    "integrity": {"algorithm": "sha256", "digest": _digest(checkpoint.to_dict())}}
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, envelope)
        return path

    def load_checkpoint(self, task_id: str, checkpoint_id: str) -> Checkpoint:
        path = self._checkpoints_dir(task_id) / f"{checkpoint_id}.json"
        if not path.is_file():
            raise TaskNotFoundError(f"checkpoint {checkpoint_id!r} not found for task {task_id!r}")
        payload = _load_envelope(path)
        try:
            return Checkpoint.from_dict(payload)
        except ValidationError as exc:
            raise TaskIntegrityError(f"{path}: checkpoint failed schema validation: {exc}") from None

    def list_checkpoints(self, task_id: str) -> List[Checkpoint]:
        directory = self._checkpoints_dir(task_id)
        if not directory.is_dir():
            return []
        checkpoints = []
        for path in sorted(directory.glob("*.json")):
            payload = _load_envelope(path)
            try:
                checkpoints.append(Checkpoint.from_dict(payload))
            except ValidationError as exc:
                raise TaskIntegrityError(f"{path}: checkpoint failed schema validation: {exc}") from None
        return checkpoints

    # -- journals -----------------------------------------------------------

    def journal_for(self, task_id: str) -> Journal:
        """The hash-chained action journal for a task (Phase 4)."""
        from .journal import Journal
        return Journal(self._task_dir(task_id) / "journal.jsonl")
