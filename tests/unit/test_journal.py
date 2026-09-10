"""Hash-chained Action Journal tests (SECURITY.md s22.2)."""
import json
import tempfile
import unittest
from pathlib import Path

from continuity.journal import (
    GENESIS,
    Journal,
    JournalError,
    JournalIntegrityError,
)


class JournalTests(unittest.TestCase):

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.journal = Journal(self.root / "task-1" / "journal.jsonl")

    def test_empty_journal_has_no_records(self):
        self.assertEqual(self.journal.records(), [])
        self.assertTrue(self.journal.verify())

    def test_first_record_links_genesis(self):
        record = self.journal.append("POLICY_DECISION", {"decision": "ALLOW"})
        self.assertEqual(record["sequence"], 0)
        self.assertEqual(record["previousHash"], GENESIS)
        self.assertTrue(self.journal.verify())

    def test_chain_is_linked(self):
        first = self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        second = self.journal.append("ACTION_TERMINAL", {"actionId": "a1"})
        self.assertEqual(second["previousHash"], first["hash"])
        self.assertEqual(second["sequence"], 1)
        self.assertTrue(self.journal.verify())

    def test_records_survive_reopen(self):
        self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        reopened = Journal(self.journal.path)
        self.assertEqual(len(reopened.records()), 1)
        self.assertTrue(reopened.verify())

    def test_tampered_payload_detected(self):
        self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        lines = self.journal.path.read_text().splitlines()
        data = json.loads(lines[0])
        data["payload"]["actionId"] = "HACKED"
        self.journal.path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        self.assertFalse(self.journal.verify())
        with self.assertRaises(JournalIntegrityError):
            self.journal.records()

    def test_tampered_hash_detected(self):
        self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        lines = self.journal.path.read_text().splitlines()
        data = json.loads(lines[0])
        data["hash"] = "0" * 64
        self.journal.path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        self.assertFalse(self.journal.verify())

    def test_broken_linkage_detected(self):
        first = self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        second = self.journal.append("ACTION_TERMINAL", {"actionId": "a1"})
        lines = self.journal.path.read_text().splitlines()
        data = json.loads(lines[1])
        data["previousHash"] = "0" * 64
        self.journal.path.write_text(lines[0] + "\n" + json.dumps(data) + "\n", encoding="utf-8")
        self.assertFalse(self.journal.verify())
        with self.assertRaises(JournalIntegrityError):
            self.journal.records()

    def test_malformed_line_detected(self):
        self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        with open(self.journal.path, "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        self.assertFalse(self.journal.verify())

    def test_count_event_type(self):
        self.journal.append("POLICY_DECISION", {"decision": "ALLOW"})
        self.journal.append("ACTION_STARTED", {"actionId": "a1"})
        self.journal.append("ACTION_STARTED", {"actionId": "a2"})
        self.journal.append("ACTION_TERMINAL", {"actionId": "a1"})
        self.assertEqual(self.journal.count_event_type("ACTION_STARTED"), 2)
        self.assertEqual(self.journal.count_event_type("POLICY_DECISION"), 1)

    def test_consumed_approval_references(self):
        self.journal.append("APPROVAL_RESULT", {"approvalReference": "appr-1", "decision": "APPROVE"})
        self.assertEqual(self.journal.consumed_approval_references(), {"appr-1"})

    def test_write_failure_raises(self):
        journal = Journal(self.root / "nonexistent-dir-deeper" / "x" / "journal.jsonl")
        # parent creation is attempted by append; simulate failure with an
        # unwritable location by making the path a directory instead.
        blocker = self.root / "blocker"
        blocker.mkdir()
        journal = Journal(blocker / "journal.jsonl")
        # make the file path a directory so open() fails
        (blocker / "journal.jsonl").mkdir()
        with self.assertRaises(JournalError):
            journal.append("ACTION_STARTED", {"actionId": "a1"})


if __name__ == "__main__":
    unittest.main()
