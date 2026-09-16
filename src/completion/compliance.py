"""Completion-time compliance checks over authoritative durable records.

VERIFICATION.md s13-s16: before DONE, the Completion Engine must verify
Policy and resource compliance from authoritative records - never from the
acting model's report. The Action Journal is the authoritative record
source: every ACTION_STARTED must be audited by a preceding non-DENY
POLICY_DECISION (and a valid approval when ASK), and resource consumption
is derived from verified journal records against the task's externally
enforced limits. Missing or unverifiable records fail closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from core import Task, parse_iso

from continuity.journal import Journal

# resource-limit dimensions without durable v1 records: a set limit cannot
# be proven respected, so compliance is INCONCLUSIVE (s15/s16). modelCalls
# is recorded from Phase 11 (MODEL_CALL journal events).
_UNRECORDED_DIMENSIONS = ("retryCount", "delegationCount", "network", "storage")


@dataclass
class ComplianceResult:
    """Structured outcome of one compliance check (policy or resource)."""
    compliant: bool
    problems: List[Tuple[str, str]] = field(default_factory=list)  # (reasonCode, detail)
    checks: dict = field(default_factory=dict)


def policy_compliance(task: Task, journal: Journal) -> ComplianceResult:
    """s13: every executed action must be policy-audited; approval flows
    must be recorded. Journal chain failures raise JournalError to the
    caller (they are record-integrity failures, not mere non-compliance)."""
    problems: List[Tuple[str, str]] = []
    records = journal.records()  # chain-verified; raises on tampering
    action_indexes = [i for i, r in enumerate(records)
                      if r["eventType"] == "ACTION_STARTED"]
    for position, index in enumerate(action_indexes):
        action = records[index]
        window_start = action_indexes[position - 1] + 1 if position > 0 else 0
        window = records[window_start:index]
        operation = action["payload"].get("operationId")
        decisions = [r for r in window if r["eventType"] == "POLICY_DECISION"]
        if not decisions:
            problems.append(("POLICY_COMPLIANCE_FAILED",
                             f"ACTION_STARTED for {operation!r} has no POLICY_DECISION audit"))
            continue
        governing = decisions[-1]  # the decision immediately governing this action
        payload = governing["payload"]
        if payload.get("operationId") != operation:
            problems.append(("POLICY_COMPLIANCE_FAILED",
                             f"ACTION_STARTED for {operation!r} audited for "
                             f"{payload.get('operationId')!r}"))
            continue
        value = payload.get("decision")
        if value == "DENY":
            problems.append(("POLICY_COMPLIANCE_FAILED",
                             f"ACTION_STARTED for {operation!r} follows a DENY"))
            continue
        if value == "ASK":
            asked = [r for r in window if r["eventType"] == "APPROVAL_REQUESTED"]
            approved = [r for r in window
                        if r["eventType"] == "APPROVAL_RESULT"
                        and r["payload"].get("decision") == "APPROVE"]
            if not asked or not approved:
                problems.append(("POLICY_COMPLIANCE_FAILED",
                                 f"ASK action {operation!r} lacks approval records"))
    return ComplianceResult(
        compliant=not problems,
        problems=problems,
        checks={"actions": len(action_indexes),
                "policyDecisions": len(records)})


def resource_compliance(task: Task, journal: Journal) -> ComplianceResult:
    """s15: journal-derived consumption must respect the externally
    enforced limits. Journal chain failures raise JournalError to the
    caller."""
    problems: List[Tuple[str, str]] = []
    checks: Dict = {}
    limits = task.resourceLimits
    records = journal.records()
    used = sum(1 for r in records if r["eventType"] == "ACTION_STARTED")
    checks["actionStepsUsed"] = used
    if limits.actionSteps is not None:
        checks["actionStepsLimit"] = limits.actionSteps
        if used > limits.actionSteps:
            problems.append(("RESOURCE_LIMIT_EXCEEDED",
                             f"{used} action steps exceed limit {limits.actionSteps}"))
    model_calls = sum(1 for r in records if r["eventType"] == "MODEL_CALL")
    checks["modelCallsUsed"] = model_calls
    if limits.modelCalls is not None:
        checks["modelCallsLimit"] = limits.modelCalls
        if model_calls > limits.modelCalls:
            problems.append(("RESOURCE_LIMIT_EXCEEDED",
                             f"{model_calls} model calls exceed limit {limits.modelCalls}"))
    if limits.wallClockTime is not None:
        last_ts = records[-1]["timestamp"] if records else task.createdAt
        started = parse_iso(task.createdAt)
        ended = parse_iso(last_ts)
        if started is None or ended is None:
            problems.append(("RESOURCE_RECORD_UNAVAILABLE",
                             "cannot parse task timestamps for wallClock accounting"))
        else:
            elapsed = (ended - started).total_seconds()
            checks["wallClockUsed"] = elapsed
            checks["wallClockLimit"] = limits.wallClockTime
            if elapsed > limits.wallClockTime:
                problems.append(("RESOURCE_LIMIT_EXCEEDED",
                                 f"wallClock {elapsed:.0f}s exceeds limit "
                                 f"{limits.wallClockTime}s"))
    for dimension in _UNRECORDED_DIMENSIONS:
        if getattr(limits, dimension) is not None:
            problems.append(("RESOURCE_RECORD_UNAVAILABLE",
                             f"no durable record exists for {dimension} consumption"))
    return ComplianceResult(compliant=not problems, problems=problems, checks=checks)
