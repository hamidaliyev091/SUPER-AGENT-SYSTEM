"""Completion Engine (Phase 6).

Implements INTERFACES.md s20 and VERIFICATION.md s13-s19. This is the ONLY
component authorized to transition a task into DONE.

evaluate(taskId) checks every applicable DONE condition (s19) against
authoritative durable records - never against the acting model's report:

1. journal integrity (policy audit records must be readable and verified);
2. task state is VERIFYING (the lifecycle's completion state);
3. a valid Completion Contract exists (s4/s5);
4. policy/rule versions are known (s13.6);
5. no unresolved critical failure (s19.13);
6. a durable, integrity-validated VerificationResult exists per criterion;
7. policy compliance passes (s13/s14);
8. resource compliance passes (s15/s16);
9. no action was recorded after a criterion's last verification (s20);
10. evidence provenance references actions present in the verified journal;
11. every mandatory criterion PASSes with evidence and sufficient
    independence (s19.2-s19.8); INCONCLUSIVE never produces DONE.

A DONE decision is journaled, durably stored behind an integrity envelope,
reloaded and integrity-validated, and only then applied via
TaskManager.transition(by_completion_engine=True) (V-PRINCIPLE-05, s19.17).

Reason codes follow VERIFICATION.md s18 plus two extensions:
COMPLETION_CONTRACT_INVALID and VERIFICATION_RECORD_INTEGRITY_FAILED.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from core import (
    CompletionContract,
    CompletionDecision,
    Task,
    ValidationError,
    utcnow_iso,
)
from core.enums import (
    CompletionDecisionValue,
    IndependenceLevel,
    RiskLevel,
    SideEffectState,
    TaskState,
    VerificationStatus,
)
from core.validation import validate_completion_contract
from core.versions import TASK_SCHEMA_VERSION

from continuity.journal import JournalError
from continuity.task_store import _digest

from verification.independence import _LEVEL_RANK
from verification.result_store import VerificationResultError, VerificationResultStore

from .compliance import policy_compliance, resource_compliance
from .store import CompletionDecisionStore


class CompletionError(Exception):
    """Completion authorization failed partway; durable records may show a
    DONE decision that was not applied (a Phase 7 recovery concern)."""


class CompletionEngine:
    """The only path to DONE."""

    def __init__(self, task_manager, store):
        self.task_manager = task_manager
        self.store = store
        self.results = VerificationResultStore(store.root)
        self.decisions = CompletionDecisionStore(store.root)

    # -- INTERFACES.md s20 -----------------------------------------------------

    def evaluate(self, taskId: str) -> CompletionDecision:
        """Evaluate all DONE requirements against authoritative durable
        records and return the structured decision. When the decision is
        DONE it is persisted, integrity-validated, and applied before this
        method returns."""
        task = self.task_manager.get_task(taskId)
        journal = self.store.journal_for(taskId)
        try:
            journal.records()
        except JournalError as exc:
            return self._decision(task, CompletionDecisionValue.BLOCKED,
                                  "POLICY_RECORD_INTEGRITY_FAILED",
                                  f"journal integrity failure: {exc}")

        if task.state is not TaskState.VERIFYING:
            return self._decision(
                task, CompletionDecisionValue.CONTINUE,
                "VERIFICATION_INCONCLUSIVE",
                f"task is {task.state.value}; DONE requires VERIFYING")

        contract = task.completionContract
        if contract is None:
            return self._decision(task, CompletionDecisionValue.BLOCKED,
                                  "COMPLETION_CONTRACT_INVALID",
                                  "task has no Completion Contract")
        problems = validate_completion_contract(contract)
        if problems:
            return self._decision(task, CompletionDecisionValue.BLOCKED,
                                  "COMPLETION_CONTRACT_INVALID",
                                  "; ".join(problems))

        if not (task.policyContext.policyVersion.strip()
                and task.policyContext.ruleVersion.strip()):
            return self._decision(task, CompletionDecisionValue.BLOCKED,
                                  "POLICY_RECORD_UNAVAILABLE",
                                  "policy/rule versions are not known")

        critical = [f for f in task.failures
                    if f.sideEffectState is SideEffectState.UNKNOWN
                    or f.category == "CRITICAL"]
        if critical:
            return self._decision(
                task, CompletionDecisionValue.FAILED, "CRITICAL_FAILURE_PRESENT",
                f"{len(critical)} unresolved critical failure(s)")

        criterion_results: Dict[str, object] = {}
        evidence_refs: List[str] = []
        verification_refs: List[str] = []
        for criterion in contract.successCriteria:
            try:
                result = self.results.load(taskId, criterion.id)
            except VerificationResultError:
                return self._decision(
                    task, CompletionDecisionValue.CONTINUE,
                    "VERIFICATION_INCONCLUSIVE",
                    f"no durable verification result for criterion {criterion.id!r}")
            except Exception as exc:  # TaskIntegrityError and friends
                return self._decision(
                    task, CompletionDecisionValue.BLOCKED,
                    "VERIFICATION_RECORD_INTEGRITY_FAILED",
                    f"verification record for {criterion.id!r}: {exc}")
            criterion_results[criterion.id] = result
            evidence_refs.extend(e.evidenceId for e in result.evidence)
            digest = (result.integrityMetadata or {}).get("digest", "undigested")
            verification_refs.append(f"{criterion.id}:{digest}")

        policy = policy_compliance(task, journal)
        if not policy.compliant:
            code, detail = policy.problems[0]
            return self._decision(task, CompletionDecisionValue.BLOCKED, code, detail)

        resources = resource_compliance(task, journal)
        if not resources.compliant:
            code, detail = resources.problems[0]
            if code == "RESOURCE_LIMIT_EXCEEDED":
                return self._decision(task, CompletionDecisionValue.FAILED, code, detail)
            return self._decision(task, CompletionDecisionValue.BLOCKED, code, detail)

        for criterion_id, _ in criterion_results.items():
            if self._stale(criterion_id, journal):
                return self._decision(
                    task, CompletionDecisionValue.CONTINUE,
                    "RECOVERY_REQUIRES_REVERIFICATION",
                    f"criterion {criterion_id!r} was verified before later "
                    "actions; re-verify")

        provenance_problem = self._evidence_provenance(criterion_results, journal)
        if provenance_problem is not None:
            return self._decision(task, CompletionDecisionValue.BLOCKED,
                                  "POLICY_RECORD_UNAVAILABLE", provenance_problem)

        for criterion in contract.successCriteria:
            if not criterion.mandatory:
                continue
            result = criterion_results[criterion.id]
            if result.result is VerificationStatus.FAIL:
                return self._decision(
                    task, CompletionDecisionValue.REPAIR, "MANDATORY_CRITERION_FAILED",
                    f"mandatory criterion {criterion.id!r} FAILed")
            if result.result is VerificationStatus.INCONCLUSIVE:
                return self._decision(
                    task, CompletionDecisionValue.CONTINUE, "VERIFICATION_INCONCLUSIVE",
                    f"mandatory criterion {criterion.id!r} is INCONCLUSIVE")
            if not result.evidence:
                return self._decision(
                    task, CompletionDecisionValue.CONTINUE, "VERIFICATION_INCONCLUSIVE",
                    f"mandatory criterion {criterion.id!r} has no evidence")
            required = self._required_independence(criterion, contract)
            if _LEVEL_RANK[result.independenceLevel] < _LEVEL_RANK[required]:
                return self._decision(
                    task, CompletionDecisionValue.WAITING_USER,
                    "INDEPENDENT_EVIDENCE_MISSING",
                    f"criterion {criterion.id!r} evidence level "
                    f"{result.independenceLevel.value} below required {required.value}")

        # All applicable DONE conditions are satisfied: authorize DONE.
        decision = self._decision(
            task, CompletionDecisionValue.DONE, "ALL_CRITERIA_PASSED",
            "all mandatory success criteria passed with compliant policy "
            "and resource records",
            criterion_results={cid: r.result.value
                              for cid, r in criterion_results.items()},
            policy_compliance={"compliant": True, "checks": policy.checks},
            resource_compliance={"compliant": True, "checks": resources.checks},
            evidence_references=evidence_refs,
            verification_references=verification_refs,
            policy_version=task.policyContext.policyVersion,
        )
        return self._persist_and_transition(task, decision)

    # -- DONE authorization ---------------------------------------------------

    def _persist_and_transition(self, task: Task, decision: CompletionDecision) -> CompletionDecision:
        """V-PRINCIPLE-05: journal the decision, store it behind an
        integrity envelope, reload it (integrity-validated), and only then
        transition the task to DONE."""
        journal = self.store.journal_for(task.id)
        decision.integrityMetadata = {"algorithm": "SHA-256",
                                      "digest": _digest(decision.to_dict())}
        journal.append("COMPLETION_DECISION", {
            "decision": decision.decision.value,
            "reasonCode": decision.reasonCode,
            "criterionResults": decision.criterionResults,
            "decisionDigest": decision.integrityMetadata["digest"],
        })
        self.decisions.save(decision)
        reloaded = self.decisions.load(task.id)
        if reloaded.decision is not CompletionDecisionValue.DONE:
            raise CompletionError("persisted completion decision does not authorize DONE")
        try:
            self.task_manager.transition(task.id, TaskState.DONE,
                                         by_completion_engine=True)
        except ValidationError as exc:
            raise CompletionError(f"DONE transition rejected: {exc}") from None
        return decision

    # -- checks ----------------------------------------------------------------

    @staticmethod
    def _stale(criterion_id: str, journal) -> bool:
        """s20: any ACTION_STARTED recorded after a criterion's last
        VERIFICATION_RESULT invalidates it for completion purposes."""
        last_result_seq = None
        for record in journal.records():
            event = record["eventType"]
            if event == "VERIFICATION_RESULT" and \
                    record["payload"].get("criterionId") == criterion_id:
                last_result_seq = record["sequence"]
            elif event == "ACTION_STARTED" and last_result_seq is not None \
                    and record["sequence"] > last_result_seq:
                return True
        return False

    @staticmethod
    def _evidence_provenance(criterion_results, journal) -> Optional[str]:
        """Engine-collected evidence must reference an action that exists
        in the verified journal (its durable policy audit)."""
        action_ids = set()
        for record in journal.records():
            if record["eventType"] == "ACTION_STARTED":
                action_ids.add(record["payload"].get("actionId"))
        for result in criterion_results.values():
            for evidence in result.evidence:
                provenance = evidence.provenance or ""
                if provenance.startswith("policy-pipeline:"):
                    action_id = provenance.split(":", 1)[1]
                    if action_id not in action_ids:
                        return (f"evidence {evidence.evidenceId} references "
                                f"action {action_id!r} absent from the journal")
        return None

    @staticmethod
    def _required_independence(criterion, contract: CompletionContract) -> IndependenceLevel:
        levels = [IndependenceLevel.LEVEL_0_SELF]
        if criterion.riskLevel in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            levels.append(IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME)
        if contract.requiredIndependenceLevel is not None:
            levels.append(contract.requiredIndependenceLevel)
        return max(levels, key=lambda level: _LEVEL_RANK[level])

    @staticmethod
    def _decision(task: Task, decision, reason_code: str, reason: str,
                  criterion_results=None, policy_compliance=None,
                  resource_compliance=None, evidence_references=None,
                  verification_references=None, policy_version=None) -> CompletionDecision:
        return CompletionDecision(
            taskId=task.id,
            decision=decision,
            schemaVersion=TASK_SCHEMA_VERSION,
            timestamp=utcnow_iso(),
            reasonCode=reason_code,
            reason=reason,
            criterionResults=criterion_results,
            policyCompliance=policy_compliance,
            resourceCompliance=resource_compliance,
            evidenceReferences=list(evidence_references or []),
            verificationReferences=list(verification_references or []),
            policyVersion=policy_version,
            integrityMetadata=None,
        )
