"""Human approval interface (INTERFACES.md s21).

The pipeline never collects approval itself; it calls a HumanApproval port.
Policy validates the resulting ApprovalResult (expiry, reference, decision);
the pipeline additionally enforces single-use consumption via the journal.
"""
from __future__ import annotations

import queue
from typing import Optional

from core import ApprovalRequest, ApprovalResult, ActorIdentity
from core.enums import ApprovalDecision, ActorType


class HumanApproval:
    """Port for collecting explicit, scoped, time-bounded human approvals."""

    def request(self, approval_request: ApprovalRequest) -> Optional[ApprovalResult]:
        raise NotImplementedError


class QueueApprover(HumanApproval):
    """Test/local approver: reads preloaded ApprovalResults from a queue.

    When the queue is empty it returns None (timeout semantics: no response
    is never approval; POLICY.md s15). Production runtimes replace this with
    a UI-backed implementation.
    """

    def __init__(self):
        self._queue = queue.Queue()

    def enqueue(self, decision: ApprovalDecision, approver_id: str = "user") -> None:
        """Enqueue a pre-made response; the pipeline pairs it with the next
        pending approval request in queue order."""
        self._queue.put((decision, approver_id))

    def request(self, approval_request: ApprovalRequest) -> Optional[ApprovalResult]:
        try:
            decision, approver_id = self._queue.get_nowait()
        except queue.Empty:
            return None
        return ApprovalResult(
            approvalReference=approval_request.approvalReference,
            decision=decision,
            approverIdentity=ActorIdentity(actorId=approver_id, actorType=ActorType.USER),
            timestamp=approval_request.expiresAt,
        )
