"""Durable observation records and their artifacts (Phase 14).

An observation is what the system actually saw: the result of an executed
action, plus any file that action produced (a screenshot, an exported
report). Observations are the loop's memory - the acting model reasons
about them on the next turn - so they must be as durable and as
tamper-evident as the rest of the authoritative state.

Layout (beside the other per-task state, ADR-003)::

    <root>/
      <task-id>/
        observations/
          <sequence>.json      enveloped, SHA-256 protected
        artifacts/
          <sha256>.png         content-addressed, written once

Design rules:

- Every record is wrapped in the same integrity envelope the task store
  uses; a tampered or corrupted record raises TaskIntegrityError rather
  than returning a plausible-looking history (CONTINUITY.md s20).
- Artifacts are content-addressed and immutable: the file name IS the
  digest, so a re-written artifact is the same bytes or a different file.
- Reads are bounded (`recent`) and retention is bounded (`sweep`), so a
  long-running task cannot grow without limit (SECURITY.md resource
  discipline). Nothing is deleted while a retained record references it.
- An observation is data, never authority. It never carries a policy
  decision, never satisfies a success criterion by itself, and the model
  cannot append to this store - only the execution path records.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import List, Optional, Union

from core import utcnow_iso

from .task_store import (  # noqa: F401 - TaskIntegrityError is re-exported
    TaskIntegrityError,
    _atomic_write_json,
    _digest,
    _load_envelope,
)

#: Observation kinds recorded by the execution path.
KIND_ACTION = "action"
KIND_VISION = "vision.description"

#: Default retention for records and their artifacts.
DEFAULT_KEEP = 40


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObservationStore:
    """Durable, integrity-protected observations and content-addressed
    artifacts for one task store root."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)

    def _task_dir(self, task_id: str) -> Path:
        return self.root / task_id

    def _observations_dir(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "observations"

    def _artifacts_dir(self, task_id: str) -> Path:
        return self._task_dir(task_id) / "artifacts"

    # -- artifacts ----------------------------------------------------------

    def write_artifact(self, task_id: str, data: bytes,
                       extension: str = ".png") -> dict:
        """Persist artifact bytes under their own digest. Idempotent: the
        same bytes always land in the same file. Returns a bounded
        descriptor - never the bytes themselves."""
        if not isinstance(data, (bytes, bytearray)):
            raise ValueError("artifact data must be bytes")
        raw = bytes(data)
        digest = _sha256(raw)
        directory = self._artifacts_dir(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}{extension}"
        if not path.is_file():
            tmp = path.with_name(path.name + ".tmp")
            with open(tmp, "wb") as handle:
                handle.write(raw)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        return {"path": str(path), "sha256": digest, "bytes": len(raw)}

    def read_artifact(self, task_id: str, digest: str,
                      extension: str = ".png") -> Optional[bytes]:
        """Artifact bytes, or None when absent. The digest is re-verified
        on read: a corrupted artifact is never returned as valid."""
        path = self._artifacts_dir(task_id) / f"{digest}{extension}"
        if not path.is_file():
            return None
        raw = path.read_bytes()
        return raw if _sha256(raw) == digest else None

    # -- records ------------------------------------------------------------

    def record(self, task_id: str, kind: str, summary: str, data: dict,
               action_id: Optional[str] = None,
               artifact: Optional[dict] = None) -> dict:
        """Durably append one observation record and return it."""
        directory = self._observations_dir(task_id)
        directory.mkdir(parents=True, exist_ok=True)
        record = {
            "observationId": uuid.uuid4().hex,
            "taskId": task_id,
            "actionId": action_id,
            "kind": kind,
            "timestamp": utcnow_iso(),
            "summary": summary,
            "data": data,
            "artifact": artifact,
        }
        # Colons are dropped from the file name so it stays usable from a
        # shell; the order is still the record order (ISO timestamps sort).
        stamp = record["timestamp"].replace(":", "")
        path = directory / f"{stamp}-{record['observationId']}.json"
        _atomic_write_json(path, {
            "payload": record,
            "integrity": {"algorithm": "sha256", "digest": _digest(record)},
        })
        return record

    def all_records(self, task_id: str) -> List[dict]:
        """Every retained observation, oldest first. Integrity failures
        raise; they are never silently skipped."""
        directory = self._observations_dir(task_id)
        if not directory.is_dir():
            return []
        records = []
        for path in sorted(directory.glob("*.json")):
            records.append(_load_envelope(path))
        return records

    def recent(self, task_id: str, limit: int = 5) -> List[dict]:
        """The most recent `limit` observations, oldest first (reading
        order). Bounded so replay into a model request cannot grow."""
        if limit <= 0:
            return []
        return self.all_records(task_id)[-limit:]

    def latest(self, task_id: str) -> Optional[dict]:
        records = self.recent(task_id, limit=1)
        return records[0] if records else None

    # -- retention ----------------------------------------------------------

    def sweep(self, task_id: str, keep: int = DEFAULT_KEEP) -> int:
        """Drop observation records beyond `keep` and any artifact no
        retained record references. Returns the number of records removed.

        Retention is bounded on purpose: the loop must not be able to fill
        the device by observing forever. A record that cannot be read is
        left alone rather than deleted on a guess.
        """
        directory = self._observations_dir(task_id)
        if not directory.is_dir():
            return 0
        paths = sorted(directory.glob("*.json"))
        surplus = paths[:-keep] if keep > 0 else paths
        removed = 0
        for path in surplus:
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        self.sweep_artifacts(task_id)
        return removed

    def sweep_artifacts(self, task_id: str) -> int:
        """Delete artifacts no retained observation references."""
        directory = self._artifacts_dir(task_id)
        if not directory.is_dir():
            return 0
        referenced = set()
        for record in self.all_records(task_id):
            artifact = record.get("artifact")
            if isinstance(artifact, dict) and artifact.get("path"):
                referenced.add(Path(artifact["path"]).name)
        removed = 0
        for path in directory.iterdir():
            if path.name.endswith(".tmp") or path.name in referenced:
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError:
                continue
        return removed


def render_observation(record: dict, max_chars: int = 1200) -> str:
    """One observation as bounded text for a model request. Everything a
    model reads is data it may reason about and may not obey."""
    parts = [f"[{record.get('kind', 'action')}] {record.get('summary', '')}"]
    artifact = record.get("artifact")
    if isinstance(artifact, dict) and artifact.get("path"):
        parts.append(f"artifact: {artifact['path']} ({artifact.get('bytes', 0)} bytes, "
                     f"sha256 {str(artifact.get('sha256', ''))[:12]}...)")
    data = record.get("data")
    if data:
        try:
            rendered = json.dumps(data, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            rendered = str(data)
        if len(rendered) > max_chars:
            rendered = rendered[:max_chars] + f"...[truncated {len(rendered) - max_chars} chars]"
        parts.append(rendered)
    return "\n".join(parts)
