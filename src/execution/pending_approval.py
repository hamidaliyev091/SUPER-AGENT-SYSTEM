"""Durable human approval (Phase 14 WS7; ADR-021).

Policy says ASK; the pipeline asks a HumanApproval port; a port that answers
"no one is here" fails closed (POLICY.md s15). For a real operator that is
not enough: the question has to survive the pause, and the answer has to be
explicit, scoped, time-bounded, single-use and auditable.

PendingApprovalStore keeps both halves durably, in the task's own directory
beside the rest of the authoritative state (ADR-003)::

    <root>/<task-id>/approvals/<approval-reference>.json

One enveloped record per approval reference, moving through

    PENDING -> GRANTED | DENIED        (a human decided)
    GRANTED -> consumed                (the grant was used, once)

DurableApprover implements the existing HumanApproval port on top of that
store. A matching unconsumed, unexpired grant becomes an ApprovalResult
carrying the CURRENT request reference, so PolicyEngine.validate_approval and
the pipeline's single-use journal semantics are untouched; otherwise the
request is persisted and no approval is returned, which the pipeline turns
into a DENY and the orchestrator into a pause in WAITING_USER.

Nothing here authorizes anything. Policy still decides ASK, the pipeline
still validates the ApprovalResult, and a grant is only ever an answer to a
question policy already asked. The arguments are stored verbatim because a
human cannot give informed approval to a description they cannot see; the
action journal keeps storing only their hash.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, List, Optional, Union

from core import (
    ActorIdentity,
    ApprovalRequest,
    ApprovalResult,
    ValidationError,
    parse_iso,
    utcnow_iso,
)
from core.enums import ActorType, ApprovalDecision

from continuity.task_store import (  # noqa: F401 - TaskIntegrityError is re-exported
    TaskIntegrityError,
    _atomic_write_json,
    _digest,
    _load_envelope,
)

PENDING = "PENDING"
GRANTED = "GRANTED"
DENIED = "DENIED"

#: Statuses a grant can still be claimed from.
_CLAIMABLE = (GRANTED,)


def _canonical(value) -> str:
    """Canonical text of a value, so 'the same arguments' is exactly that."""
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return str(value)


def _expired(timestamp: Optional[str], now=None) -> bool:
    """True when `timestamp` is absent or already in the past."""
    moment = parse_iso(timestamp) if isinstance(timestamp, str) else None
    if moment is None:
        return True
    reference = now or utcnow_iso()
    now_moment = parse_iso(reference)
    if now_moment is None:
        return True
    if moment.tzinfo is None or now_moment.tzinfo is None:
        # naive/aware comparison would raise; both sides come from utcnow_iso
        return moment <= now_moment
    return now_moment >= moment


class PendingApprovalStore:
    """Durable approval requests and grants for one task store root."""

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root)

    def _directory(self, task_id: str) -> Path:
        return self.root / task_id / "approvals"

    def _path(self, task_id: str, reference: str) -> Path:
        return self._directory(task_id) / f"{reference}.json"

    def _write(self, task_id: str, record: dict) -> dict:
        self._directory(task_id).mkdir(parents=True, exist_ok=True)
        _atomic_write_json(self._path(task_id, record["approvalReference"]), {
            "payload": record,
            "integrity": {"algorithm": "sha256", "digest": _digest(record)},
        })
        return record

    def _read(self, task_id: str, reference: str) -> Optional[dict]:
        path = self._path(task_id, reference)
        if not path.is_file():
            return None
        return _load_envelope(path)

    # -- requests ------------------------------------------------------------

    def request(self, approval: ApprovalRequest,
                arguments: Optional[dict] = None) -> dict:
        """Persist a request for human approval, unless it is already known.

        The request is the authoritative snapshot of what policy asked about:
        operation, canonical target, arguments, expiry and explanation.
        """
        existing = self._read(approval.taskId, approval.approvalReference)
        if existing is not None:
            return existing
        target = approval.target
        record = {
            "approvalReference": approval.approvalReference,
            "taskId": approval.taskId,
            "operation": approval.operation,
            "targetType": target.type.value,
            "targetValue": target.value,
            "riskLevel": approval.riskLevel.value,
            "sideEffect": approval.sideEffect.value,
            "reversibility": approval.reversibility.value,
            "idempotency": approval.idempotency.value,
            "arguments": arguments if arguments is not None else (approval.arguments or {}),
            "explanation": approval.explanation,
            "consequences": approval.consequences,
            "requestedAt": utcnow_iso(),
            "requestExpiresAt": approval.expiresAt,
            "status": PENDING,
            "decidedAt": None,
            "decidedBy": None,
            "grantExpiresAt": None,
            "denialReason": None,
            "consumedAt": None,
        }
        return self._write(approval.taskId, record)

    def get(self, task_id: str, reference: str) -> Optional[dict]:
        return self._read(task_id, reference)

    def all_records(self, task_id: str) -> List[dict]:
        """Every approval record, oldest first. Integrity failures raise."""
        directory = self._directory(task_id)
        if not directory.is_dir():
            return []
        records = [_load_envelope(path) for path in sorted(directory.glob("*.json"))]
        return sorted(records, key=lambda r: r.get("requestedAt") or "")

    def pending(self, task_id: str) -> List[dict]:
        """Requests still waiting for a human answer, oldest first."""
        return [r for r in self.all_records(task_id) if r.get("status") == PENDING]

    def has_pending(self, task_id: str) -> bool:
        return bool(self.pending(task_id))

    def claims(self, task_id: str) -> List[dict]:
        """Grants that were given and not yet used."""
        return [r for r in self.all_records(task_id)
                if r.get("status") == GRANTED and not r.get("consumedAt")]

    # -- decisions -----------------------------------------------------------

    def grant(self, task_id: str, reference: str, *, approved_by: str) -> dict:
        """Approve one pending request. Refuses anything but a live, pending,
        unexpired request: a grant is an answer to a question still open."""
        record = self._require_pending(task_id, reference)
        if _expired(record.get("requestExpiresAt")):
            raise ValidationError(
                f"approval request {reference} has expired; it must be asked again")
        record["status"] = GRANTED
        record["decidedAt"] = utcnow_iso()
        record["decidedBy"] = approved_by
        # The grant can never outlive the request policy issued.
        record["grantExpiresAt"] = record.get("requestExpiresAt")
        return self._write(task_id, record)

    def deny(self, task_id: str, reference: str, *, denied_by: str,
             reason: str = "") -> dict:
        """Refuse one pending request. A denial is final for that request and
        is never a grant (the pipeline still returns DENY)."""
        record = self._require_pending(task_id, reference)
        record["status"] = DENIED
        record["decidedAt"] = utcnow_iso()
        record["decidedBy"] = denied_by
        record["denialReason"] = reason or "denied by operator"
        return self._write(task_id, record)

    def _require_pending(self, task_id: str, reference: str) -> dict:
        record = self._read(task_id, reference)
        if record is None:
            raise ValidationError(f"unknown approval reference {reference!r}")
        if record.get("status") != PENDING:
            raise ValidationError(
                f"approval {reference} is {record.get('status')}, not pending")
        return record

    # -- claiming ------------------------------------------------------------

    def find_grant(self, task_id: str, approval: ApprovalRequest,
                   arguments: Optional[dict] = None) -> Optional[dict]:
        """The newest unused, unexpired grant for exactly this action.

        Scope is the whole action: the operation, its canonical target and
        its arguments. A grant for 'open example.com' is not a grant for
        'open somewhere-else.example'.
        """
        wanted_operation = approval.operation
        target = approval.target
        wanted_type = target.type.value
        wanted_value = target.value
        wanted_arguments = _canonical(
            arguments if arguments is not None else (approval.arguments or {}))
        for record in reversed(self.all_records(task_id)):
            if record.get("status") not in _CLAIMABLE or record.get("consumedAt"):
                continue
            if _expired(record.get("grantExpiresAt")):
                continue
            if record.get("operation") != wanted_operation:
                continue
            if record.get("targetType") != wanted_type:
                continue
            if record.get("targetValue") != wanted_value:
                continue
            if _canonical(record.get("arguments") or {}) != wanted_arguments:
                continue
            return record
        return None

    def consume(self, task_id: str, reference: str) -> dict:
        """Mark a grant used. One human answer authorizes one execution."""
        record = self._read(task_id, reference)
        if record is None:
            raise ValidationError(f"unknown approval reference {reference!r}")
        if record.get("status") != GRANTED or record.get("consumedAt"):
            raise ValidationError(f"approval {reference} is not an unused grant")
        record["consumedAt"] = utcnow_iso()
        return self._write(task_id, record)


class DurableApprover:
    """HumanApproval over a durable store.

    Answers from a stored grant when one exists, and otherwise records the
    question and returns None - which the pipeline treats as a refusal, so
    an unanswered ask never becomes an execution.
    """

    def __init__(self, store: PendingApprovalStore,
                 notifier: Optional[Callable[[dict], None]] = None):
        self.store = store
        self.notifier = notifier

    def request(self, approval_request: ApprovalRequest) -> Optional[ApprovalResult]:
        arguments = approval_request.arguments or {}
        grant = self.store.find_grant(approval_request.taskId, approval_request,
                                      arguments=arguments)
        if grant is not None:
            self.store.consume(approval_request.taskId, grant["approvalReference"])
            return ApprovalResult(
                approvalReference=approval_request.approvalReference,
                decision=ApprovalDecision.APPROVE,
                approverIdentity=ActorIdentity(
                    actorId=grant.get("decidedBy") or "operator",
                    actorType=ActorType.USER,
                    taskId=approval_request.taskId),
                timestamp=utcnow_iso())
        record = self.store.request(approval_request, arguments=arguments)
        self._notify(record)
        return None

    def _notify(self, record: dict) -> None:
        """Visibility only. A notification is never an approval, and a
        notification that cannot be delivered changes nothing."""
        if self.notifier is None:
            return
        try:
            self.notifier(record)
        except Exception:
            pass
