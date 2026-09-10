"""Hash-chained Action Journal and audit log (SECURITY.md s22.2 v1 baseline).

Append-only JSONL with per-record hash chaining:

    record N = {sequence, timestamp, eventType, previousHash, payload, hash}
    hash(N) = sha256(canonical_json({sequence, eventType, previousHash, payload}))

The first record references the fixed genesis value "GENESIS". Records are
durable before they count: append() writes + fsyncs synchronously, and
raises JournalError on any write failure so callers can abort execution
(CONTINUITY.md s8.2: if STARTED cannot be persisted, the action MUST NOT
execute). Payloads MUST NOT contain plaintext secrets (POLICY_RULES.md
s43): callers store argument hashes, not raw arguments.

Design informed by OpenHands' append-only JSONL event stream (event
sourcing for replay/recovery) with the integrity chain SAS requires -
see ADR-005.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from core import ContractError

GENESIS = "GENESIS"


class JournalError(ContractError):
    """Journal write/read failure. Security-relevant: callers must fail closed."""


class JournalIntegrityError(JournalError):
    """The journal chain failed verification (tampering or corruption)."""


def _canonical_bytes(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _record_hash(sequence: int, event_type: str, previous_hash: str, payload: dict) -> str:
    body = {"sequence": sequence, "eventType": event_type,
            "previousHash": previous_hash, "payload": payload}
    return hashlib.sha256(_canonical_bytes(body)).hexdigest()


class Journal:
    """One append-only, integrity-chained journal per task."""

    def __init__(self, path: Path):
        self.path = Path(path)

    # -- appending ---------------------------------------------------------

    def append(self, event_type: str, payload: dict, timestamp: Optional[str] = None) -> dict:
        """Durably append one record and return it. Raises JournalError when
        the record cannot be persisted or the existing chain fails
        verification (a tampered journal must not accept new records)."""
        records = self._read_raw(verify=True)
        if records:
            sequence = records[-1]["sequence"] + 1
            previous_hash = records[-1]["hash"]
        else:
            sequence = 0
            previous_hash = GENESIS
        if timestamp is None:
            from core import utcnow_iso
            timestamp = utcnow_iso()
        record_hash = _record_hash(sequence, event_type, previous_hash, payload)
        record = {
            "sequence": sequence,
            "timestamp": timestamp,
            "eventType": event_type,
            "previousHash": previous_hash,
            "payload": payload,
            "hash": record_hash,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise JournalError(f"journal append failed: {exc}") from None
        return record

    # -- reading and verification ------------------------------------------

    def _read_raw(self, verify: bool) -> List[dict]:
        if not self.path.is_file():
            return []
        records: List[dict] = []
        try:
            with open(self.path, encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        raise JournalIntegrityError(
                            f"{self.path}: malformed record at line {line_number}") from None
                    if not isinstance(record, dict) or not all(
                            key in record for key in ("sequence", "eventType",
                                                      "previousHash", "payload", "hash")):
                        raise JournalIntegrityError(
                            f"{self.path}: incomplete record at line {line_number}")
                    records.append(record)
        except OSError as exc:
            raise JournalError(f"journal read failed: {exc}") from None
        if verify:
            self._verify_chain(records)
        return records

    def _verify_chain(self, records: List[dict]) -> None:
        previous = GENESIS
        for record in records:
            if record["sequence"] < 0:
                raise JournalIntegrityError(f"{self.path}: negative sequence")
            if record["previousHash"] != previous:
                raise JournalIntegrityError(f"{self.path}: broken chain at sequence {record['sequence']}")
            expected = _record_hash(record["sequence"], record["eventType"],
                                    record["previousHash"], record["payload"])
            if record["hash"] != expected:
                raise JournalIntegrityError(f"{self.path}: hash mismatch at sequence {record['sequence']}")
            previous = record["hash"]

    def records(self) -> List[dict]:
        """All records, chain-verified. Raises JournalIntegrityError on tampering."""
        return self._read_raw(verify=True)

    def verify(self) -> bool:
        try:
            self._read_raw(verify=True)
            return True
        except JournalError:
            return False

    # -- derived accounting -------------------------------------------------

    def count_event_type(self, event_type: str) -> int:
        """Number of verified records of the given type (e.g. ACTION_STARTED
        for externally enforced action budgets)."""
        return sum(1 for record in self.records() if record["eventType"] == event_type)

    def consumed_approval_references(self) -> set:
        """Approval references already consumed (single-use enforcement)."""
        return {
            record["payload"].get("approvalReference")
            for record in self.records()
            if record["eventType"] == "APPROVAL_RESULT"
        }
