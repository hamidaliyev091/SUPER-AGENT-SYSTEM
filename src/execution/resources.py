"""Minimal ResourceCoordinator (Phase 4 subset).

Two responsibilities per INTERFACES.md s23 / POLICY_RULES.md s40:
1. Budget accounting derived from the authoritative journal (action starts)
   plus task limits - externally enforced, the model cannot raise them.
2. In-process named locks (SHARED/EXCLUSIVE) for tool-declared resources.

Durable, cross-process resource locks arrive with the Phase 7 Continuity
Manager; v1 is single-process (ARCHITECTURE.md s20).
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock as ThreadLock
from typing import Dict, Tuple

from core import Task, parse_iso, utcnow_iso

from continuity.journal import Journal


@dataclass
class ResourceCheck:
    ok: bool
    reason: str = ""


class ResourceCoordinator:
    """Budget checks + process-local resource locks. Never authorizes."""

    def __init__(self):
        self._locks: Dict[str, ThreadLock] = {}
        self._registry_lock = ThreadLock()

    # -- budget accounting (journal-derived) -------------------------------

    def check_budget(self, task: Task, journal: Journal) -> ResourceCheck:
        limits = task.resourceLimits
        if limits.actionSteps is not None:
            used = journal.count_event_type("ACTION_STARTED")
            if used >= limits.actionSteps:
                return ResourceCheck(False,
                                     f"actionSteps budget exhausted ({used}/{limits.actionSteps})")
        if limits.wallClockTime is not None:
            started = parse_iso(task.createdAt)
            now = parse_iso(utcnow_iso())
            if started is None or now is None:
                return ResourceCheck(False, "cannot parse task timestamps for wallClock budget")
            elapsed = (now - started).total_seconds()
            if elapsed >= limits.wallClockTime:
                return ResourceCheck(
                    False, f"wallClockTime budget exhausted ({elapsed:.0f}s/{limits.wallClockTime}s)")
        return ResourceCheck(True)

    # -- named resource locks (in-process) ----------------------------------

    def acquire(self, task_id: str, resource: str, mode: str = "EXCLUSIVE") -> Tuple[bool, str]:
        if mode not in ("SHARED", "EXCLUSIVE"):
            return False, f"unknown lock mode {mode!r}"
        with self._registry_lock:
            lock = self._locks.setdefault(resource, ThreadLock())
        if not lock.acquire(blocking=False):
            return False, f"resource {resource!r} is busy"
        return True, ""

    def release(self, resource: str) -> None:
        with self._registry_lock:
            lock = self._locks.get(resource)
        if lock is not None:
            lock.release()
