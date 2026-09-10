"""Verification Engine (Phase 5).

Implements INTERFACES.md s19 and VERIFICATION.md:

- verify(taskId, criteria) evaluates each criterion against evidence
  collected at verification time. The acting model's claim, a tool's
  success flag, or an exit code is never treated as success by itself
  (ROADMAP.md Phase 5): a registered assessor evaluates the observed
  state against the actual requirement.
- collectEvidence(taskId, criterion) obtains evidence exclusively through
  the ExecutionPipeline, so verification cannot bypass Policy
  (VERIFICATION.md s12.1).
- assessIndependence(evidence) derives the evidence independence level
  from collector identity (s9). HIGH/CRITICAL criteria require independent
  evidence (s11) and full provenance metadata (s8); insufficiency yields
  INCONCLUSIVE, never PASS.
- Results are durably persisted behind SHA-256 integrity envelopes (s6)
  and recorded in the task's Action Journal; every invalidation is
  journaled too (s20). Any action recorded after a criterion's previous
  verification invalidates that result before re-verification, and
  verification always re-collects evidence fresh - a persisted result is
  never reused as evidence for a new pass.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from core import (
    ActionRequest,
    ActorIdentity,
    Evidence,
    SuccessCriterion,
    Target,
    Task,
    ToolResult,
    ValidationError,
    VerificationResult,
    VerificationState,
    utcnow_iso,
)
from core.enums import (
    ActorType,
    IndependenceLevel,
    RiskLevel,
    TargetType,
    TaskState,
    VerificationStatus,
)

from continuity.journal import JournalError
from continuity.task_store import TaskNotFoundError, _digest

from .independence import IndependenceResult, assess_independence, _LEVEL_RANK
from .methods import VerificationMethodSpec
from .result_store import VerificationResultStore


class VerificationError(Exception):
    """Evidence collection could not produce evidence (policy denial,
    tool failure, ...). Verification stays INCONCLUSIVE."""


@dataclass
class VerificationReport:
    """Aggregated outcome of one verify() pass (runtime value; the durable
    records are the per-criterion VerificationResults in the store and the
    journal entries)."""
    taskId: str
    status: VerificationStatus
    criterionResults: List[VerificationResult] = field(default_factory=list)
    timestamp: str = ""
    notes: List[str] = field(default_factory=list)


class VerificationEngine:
    """Owns verification logic. The orchestrator only requests verification
    (INTERFACES.md s19)."""

    def __init__(self, task_manager, pipeline, store, methods=None,
                 verifier_identity="verification-engine", separate_verifiers=None):
        self.task_manager = task_manager
        self.pipeline = pipeline
        self.store = store
        self.results = VerificationResultStore(store.root)
        self.methods: Dict[str, VerificationMethodSpec] = dict(methods or {})
        self.verifier_identity = verifier_identity
        self.separate_verifiers = frozenset(separate_verifiers or ())

    # -- INTERFACES.md s19 -----------------------------------------------------

    def verify(self, taskId: str,
               criteria: Optional[List[SuccessCriterion]] = None) -> VerificationReport:
        """Evaluate criteria against freshly collected, policy-governed
        evidence. Status is PASS only when every mandatory criterion PASSes
        with sufficient independent evidence."""
        timestamp = utcnow_iso()
        task = self.task_manager.get_task(taskId)
        selected = list(task.successCriteria) if criteria is None else list(criteria)
        report = VerificationReport(taskId=taskId,
                                    status=VerificationStatus.INCONCLUSIVE,
                                    timestamp=timestamp)
        if task.state is not TaskState.VERIFYING:
            report.notes.append(
                f"task state is {task.state.value}; verification requires VERIFYING")
            return report
        if not selected:
            report.notes.append("no success criteria to verify")
            return report
        journal = self.store.journal_for(taskId)
        for criterion in selected:
            try:
                result = self._verify_criterion(task, criterion, journal, timestamp)
                self._persist(taskId, result, journal)
            except (JournalError, OSError) as exc:
                result = self._result(
                    task, criterion, VerificationStatus.INCONCLUSIVE,
                    self._best_level([]), [], timestamp,
                    f"verification records could not be persisted: {exc}")
                report.notes.append(result.failureReason)
            report.criterionResults.append(result)
        report.status = self._overall(selected, report.criterionResults)
        self._update_task_verification(
            taskId, report.status,
            {r.criterionId: r.result for r in report.criterionResults}, timestamp)
        return report

    def collectEvidence(self, taskId: str, criterion: SuccessCriterion) -> List[Evidence]:
        """Policy-governed evidence collection. Raises VerificationError when
        collection cannot produce evidence (denial, tool failure, ...)."""
        task = self.task_manager.get_task(taskId)
        evidence, _, problem = self._collect_evidence(task, criterion)
        if problem is not None:
            raise VerificationError(problem)
        return evidence

    def assessIndependence(self, evidence,
                           requiredLevel: Optional[IndependenceLevel] = None) -> IndependenceResult:
        return assess_independence(evidence, self.verifier_identity,
                                   self.separate_verifiers, requiredLevel)

    def invalidate(self, taskId: str, criterionId: Optional[str] = None,
                   reason: str = "material mutation") -> None:
        """Conservatively invalidate one criterion or all (VERIFICATION.md
        s20): journal the invalidation, persist an INCONCLUSIVE tombstone so
        no stale result can be reused for DONE, and update task state."""
        task = self.task_manager.get_task(taskId)
        ids = ([criterionId] if criterionId is not None
               else [c.id for c in task.successCriteria])
        known = {c.id for c in task.successCriteria}
        unknown = [cid for cid in ids if cid not in known]
        if unknown:
            raise ValidationError(f"unknown criterion id: {unknown[0]}")
        journal = self.store.journal_for(taskId)
        journal.append("VERIFICATION_INVALIDATED", {
            "criterionIds": ids, "reason": reason})
        now = utcnow_iso()
        for cid in ids:
            self.results.save(VerificationResult(
                taskId=taskId, criterionId=cid,
                result=VerificationStatus.INCONCLUSIVE,
                verifier=self.verifier_identity,
                independenceLevel=IndependenceLevel.LEVEL_0_SELF,
                verificationMethod="invalidated", timestamp=now,
                evidenceFreshness=now,
                failureReason=f"invalidated: {reason}"))
        self._update_task_verification(
            taskId, VerificationStatus.INCONCLUSIVE,
            {cid: VerificationStatus.INCONCLUSIVE for cid in ids}, now)

    # -- per-criterion evaluation ---------------------------------------------

    def _verify_criterion(self, task, criterion, journal, timestamp) -> VerificationResult:
        spec = self.methods.get(criterion.verificationMethod)
        if spec is None:
            return self._result(
                task, criterion, VerificationStatus.INCONCLUSIVE,
                IndependenceLevel.LEVEL_0_SELF, [], timestamp,
                f"no verification method registered for {criterion.verificationMethod!r}")

        self._record_invalidations(task, criterion, journal)

        evidence, tool_result, problem = self._collect_evidence(task, criterion)
        if problem is not None:
            return self._result(task, criterion, VerificationStatus.INCONCLUSIVE,
                                self._best_level(evidence), evidence, timestamp,
                                problem)

        if spec.assessor is None:
            return self._result(
                task, criterion, VerificationStatus.INCONCLUSIVE,
                self._best_level(evidence), evidence, timestamp,
                f"verification method {spec.method!r} has no assessor")

        try:
            status, reason = spec.assessor(tool_result, task, criterion)
        except Exception as exc:
            return self._result(
                task, criterion, VerificationStatus.INCONCLUSIVE,
                self._best_level(evidence), evidence, timestamp,
                f"assessor raised: {exc}")

        required = self._required_independence(task, criterion, spec)
        independence = self.assessIndependence(evidence, requiredLevel=required)
        if status is VerificationStatus.PASS and not independence.sufficient:
            status = VerificationStatus.INCONCLUSIVE
            reason = (f"independent evidence required ({required.value}): "
                      f"{independence.reason or 'insufficient evidence'}")

        if criterion.riskLevel in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            provenance_problem = self._check_provenance(evidence)
            if provenance_problem is not None and status is VerificationStatus.PASS:
                status = VerificationStatus.INCONCLUSIVE
                reason = provenance_problem

        return self._result(
            task, criterion, status,
            independence.level or IndependenceLevel.LEVEL_0_SELF,
            evidence, timestamp,
            reason if status is not VerificationStatus.PASS else None)

    def _collect_evidence(self, task, criterion):
        """Returns (evidence, tool_result, problem). Collection is a normal
        pipeline action - verification does not bypass Policy (s12.1)."""
        spec = self.methods[criterion.verificationMethod]
        evidence: List[Evidence] = []
        if spec.includeActionEvidence:
            evidence.extend(self._action_evidence(task, criterion))
        if spec.toolId is None:
            return evidence, None, None
        arguments = spec.build_arguments(task, criterion)
        request = ActionRequest(
            taskId=task.id,
            actor=ActorIdentity(actorId=self.verifier_identity,
                                actorType=ActorType.VERIFIER, taskId=task.id),
            toolId=spec.toolId,
            target=self._target_for(arguments),
            arguments=arguments,
            reason=f"verification evidence for criterion {criterion.id}",
        )
        execution = self.pipeline.execute(request)
        if not execution.executed:
            reason = (execution.policyDecision.reason if execution.policyDecision
                      else "action did not execute")
            return evidence, execution.toolResult, f"evidence collection blocked: {reason}"
        tool_result = execution.toolResult
        if tool_result is None or not tool_result.success:
            return evidence, tool_result, "evidence collection action failed"
        evidence.append(self._evidence_from(criterion, spec.toolId,
                                            execution.actionId, tool_result))
        return evidence, tool_result, None

    def _evidence_from(self, criterion, tool_id, action_id, tool_result) -> Evidence:
        canonical = json.dumps(tool_result.output, sort_keys=True,
                               separators=(",", ":")).encode("utf-8")
        return Evidence(
            evidenceId=uuid.uuid4().hex,
            timestamp=tool_result.timestamp or utcnow_iso(),
            source=tool_id,
            collectorIdentity=self.verifier_identity,
            contentHash=hashlib.sha256(canonical).hexdigest(),
            provenance=f"policy-pipeline:{action_id}",
            criterionId=criterion.id,
            validationStatus="VALIDATED",
        )

    def _action_evidence(self, task, criterion) -> List[Evidence]:
        """Level 0 self-evidence: what the acting side's own journal records
        say about its actions. Authoritative about what was executed, but
        never sufficient where independent evidence is required (s9)."""
        journal = self.store.journal_for(task.id)
        records = journal.records()
        started = {}
        for record in records:
            if record["eventType"] == "ACTION_STARTED":
                started[record["payload"].get("actionId")] = record
        out = []
        for record in records:
            if record["eventType"] != "ACTION_TERMINAL":
                continue
            payload = record["payload"]
            action_id = payload.get("actionId")
            start_payload = (started.get(action_id) or {}).get("payload") or {}
            claim = {"terminalState": payload.get("terminalState"),
                     "toolSuccess": payload.get("toolSuccess")}
            out.append(Evidence(
                evidenceId=uuid.uuid4().hex,
                timestamp=record["timestamp"],
                source=start_payload.get("operationId", "acting-operation"),
                collectorIdentity=start_payload.get("actorId", "acting-model"),
                contentHash=_digest(claim),
                provenance=f"acting-operation-journal:{action_id or ''}",
                criterionId=criterion.id,
                validationStatus="NOT_INDEPENDENTLY_VALIDATED",
            ))
        return out

    # -- independence and provenance ------------------------------------------

    def _required_independence(self, task, criterion, spec) -> IndependenceLevel:
        levels = [IndependenceLevel.LEVEL_0_SELF]
        if criterion.riskLevel in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            levels.append(IndependenceLevel.LEVEL_1_INDEPENDENT_RUNTIME)
        contract = task.completionContract
        if contract is not None and contract.requiredIndependenceLevel is not None:
            levels.append(contract.requiredIndependenceLevel)
        if spec.requiredIndependenceLevel is not None:
            levels.append(spec.requiredIndependenceLevel)
        return max(levels, key=lambda level: _LEVEL_RANK[level])

    @staticmethod
    def _check_provenance(evidence) -> Optional[str]:
        """s8: HIGH/CRITICAL evidence must carry full provenance metadata.
        Returns a problem description, or None when the evidence qualifies."""
        if not evidence:
            return "HIGH/CRITICAL criterion requires evidence with provenance metadata"
        required = ("timestamp", "source", "contentHash", "collectorIdentity",
                    "provenance", "validationStatus")
        for item in evidence:
            for field in required:
                if not getattr(item, field, None):
                    return (f"evidence {item.evidenceId} lacks {field}; "
                            "required for HIGH/CRITICAL criteria")
        return None

    # -- invalidation and persistence (s20, s6) ----------------------------------

    def _record_invalidations(self, task, criterion, journal) -> None:
        """s20: if any action was recorded after this criterion's previous
        verification, journal the invalidation before re-verifying."""
        last_result_seq = None
        action_sequences = []
        for record in journal.records():
            event = record["eventType"]
            if event == "VERIFICATION_RESULT" and \
                    record["payload"].get("criterionId") == criterion.id:
                last_result_seq = record["sequence"]
            elif event == "ACTION_STARTED":
                action_sequences.append(record["sequence"])
        if last_result_seq is None:
            return
        newer = [seq for seq in action_sequences if seq > last_result_seq]
        if newer:
            journal.append("VERIFICATION_INVALIDATED", {
                "criterionIds": [criterion.id],
                "reason": (f"actions recorded after the last verification of "
                           f"{criterion.id} (conservative s20 invalidation)"),
                "actionSequences": newer,
            })

    def _persist(self, task_id, result, journal) -> None:
        """Durably record the result: journal first (append-only ledger),
        then the integrity-enveloped latest-result file."""
        result.integrityMetadata = {"algorithm": "SHA-256",
                                    "digest": _digest(result.to_dict())}
        journal.append("VERIFICATION_RESULT", {
            "taskId": task_id,
            "criterionId": result.criterionId,
            "result": result.result.value,
            "resultDigest": result.integrityMetadata["digest"],
            "evidenceCount": len(result.evidence),
            "independenceLevel": result.independenceLevel.value,
        })
        self.results.save(result)

    # -- helpers ----------------------------------------------------------------

    def _result(self, task, criterion, status, level, evidence, timestamp,
                failure_reason=None) -> VerificationResult:
        return VerificationResult(
            taskId=task.id,
            criterionId=criterion.id,
            result=status,
            verifier=self.verifier_identity,
            independenceLevel=level,
            verificationMethod=criterion.verificationMethod,
            timestamp=timestamp,
            evidence=list(evidence),
            evidenceFreshness=max((e.timestamp for e in evidence), default=None),
            failureReason=failure_reason,
            notes=None,
            integrityMetadata=None,
        )

    def _best_level(self, evidence) -> IndependenceLevel:
        return self.assessIndependence(evidence).level or IndependenceLevel.LEVEL_0_SELF

    @staticmethod
    def _target_for(arguments) -> Optional[Target]:
        if isinstance(arguments.get("path"), str):
            return Target(type=TargetType.FILESYSTEM, value=arguments["path"])
        if isinstance(arguments.get("package"), str):
            return Target(type=TargetType.PACKAGE, value=arguments["package"])
        return None

    @staticmethod
    def _overall(criteria, results) -> VerificationStatus:
        by_id = {r.criterionId: r for r in results}
        mandatory = [c for c in criteria if c.mandatory]
        if not mandatory:
            return VerificationStatus.INCONCLUSIVE
        for criterion in mandatory:
            result = by_id.get(criterion.id)
            if result is None or result.result is VerificationStatus.INCONCLUSIVE:
                return VerificationStatus.INCONCLUSIVE
            if result.result is VerificationStatus.FAIL:
                return VerificationStatus.FAIL
        return VerificationStatus.PASS

    def _update_task_verification(self, task_id, overall, criterion_map, timestamp) -> None:
        from task import UpdateTaskRequest
        task = self.task_manager.get_task(task_id)
        merged = dict(task.verification.criterionResults)
        merged.update(criterion_map)
        state = VerificationState(overall=overall, criterionResults=merged,
                                  updatedAt=timestamp)
        try:
            self.task_manager.update_task(task_id, UpdateTaskRequest(verification=state))
        except Exception:
            # Best effort: the journal and stored results are the
            # authoritative durable verification records.
            pass
