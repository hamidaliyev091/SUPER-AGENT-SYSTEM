"""Lifecycle transition tests (TASK_SCHEMA.md s22)."""
import unittest

from core import ValidationError, can_transition, validate_transition
from core.enums import TaskState


class LifecycleTests(unittest.TestCase):

    def _assert_allowed(self, current, target):
        validate_transition(current, target)
        self.assertTrue(can_transition(current, target))

    def _assert_denied(self, current, target):
        with self.assertRaises(ValidationError):
            validate_transition(current, target)
        self.assertFalse(can_transition(current, target))

    def test_documented_paths(self):
        self._assert_allowed(TaskState.CREATED, TaskState.VALIDATING)
        self._assert_allowed(TaskState.VALIDATING, TaskState.PLANNING)
        self._assert_allowed(TaskState.PLANNING, TaskState.READY)
        self._assert_allowed(TaskState.READY, TaskState.RUNNING)
        self._assert_allowed(TaskState.RUNNING, TaskState.OBSERVING)
        self._assert_allowed(TaskState.OBSERVING, TaskState.VERIFYING)
        self._assert_allowed(TaskState.VERIFYING, TaskState.REPAIRING)
        self._assert_allowed(TaskState.REPAIRING, TaskState.RUNNING)

    def test_recovery_paths(self):
        for state in (TaskState.RUNNING, TaskState.OBSERVING, TaskState.VERIFYING,
                      TaskState.REPAIRING):
            self._assert_allowed(state, TaskState.RECOVERING)
        for target in (TaskState.RUNNING, TaskState.READY, TaskState.PLANNING):
            self._assert_allowed(TaskState.RECOVERING, target)

    def test_waiting_and_blocked(self):
        for state in (TaskState.CREATED, TaskState.VALIDATING, TaskState.PLANNING,
                      TaskState.READY, TaskState.RUNNING, TaskState.OBSERVING,
                      TaskState.VERIFYING, TaskState.REPAIRING, TaskState.RECOVERING):
            self._assert_allowed(state, TaskState.WAITING_USER)
            self._assert_allowed(state, TaskState.BLOCKED)

    def test_resume_requires_recovering(self):
        self._assert_allowed(TaskState.WAITING_USER, TaskState.RECOVERING)
        self._assert_allowed(TaskState.BLOCKED, TaskState.RECOVERING)
        self._assert_denied(TaskState.WAITING_USER, TaskState.RUNNING)
        self._assert_denied(TaskState.BLOCKED, TaskState.RUNNING)

    def test_fail_and_cancel(self):
        for state in (TaskState.CREATED, TaskState.RUNNING, TaskState.VERIFYING):
            self._assert_allowed(state, TaskState.FAILED)
            self._assert_allowed(state, TaskState.CANCELLED)
        self._assert_allowed(TaskState.WAITING_USER, TaskState.CANCELLED)
        self._assert_allowed(TaskState.BLOCKED, TaskState.CANCELLED)

    def test_done_only_via_completion_engine(self):
        # Generic path can never produce DONE.
        self._assert_denied(TaskState.VERIFYING, TaskState.DONE)
        # Completion Engine path requires VERIFYING.
        with self.assertRaises(ValidationError):
            validate_transition(TaskState.RUNNING, TaskState.DONE, by_completion_engine=True)
        with self.assertRaises(ValidationError):
            validate_transition(TaskState.READY, TaskState.DONE, by_completion_engine=True)
        # The only valid DONE transition.
        validate_transition(TaskState.VERIFYING, TaskState.DONE, by_completion_engine=True)

    def test_terminal_states_are_dead_ends(self):
        for terminal in (TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED):
            for target in TaskState:
                self.assertFalse(can_transition(terminal, target))

    def test_undocumented_jumps_denied(self):
        self._assert_denied(TaskState.CREATED, TaskState.RUNNING)
        self._assert_denied(TaskState.CREATED, TaskState.DONE)
        self._assert_denied(TaskState.VALIDATING, TaskState.RUNNING)
        self._assert_denied(TaskState.READY, TaskState.VERIFYING)
        self._assert_denied(TaskState.RUNNING, TaskState.VERIFYING)
        self._assert_denied(TaskState.OBSERVING, TaskState.RUNNING)
        self._assert_denied(TaskState.VERIFYING, TaskState.RUNNING)
        self._assert_denied(TaskState.REPAIRING, TaskState.OBSERVING)
        self._assert_denied(TaskState.RECOVERING, TaskState.VERIFYING)
        self._assert_denied(TaskState.BLOCKED, TaskState.WAITING_USER)
        self._assert_denied(TaskState.WAITING_USER, TaskState.BLOCKED)

    def test_repair_loop_path(self):
        # VERIFYING -> REPAIRING -> RUNNING -> OBSERVING -> VERIFYING
        for current, target in (
            (TaskState.VERIFYING, TaskState.REPAIRING),
            (TaskState.REPAIRING, TaskState.RUNNING),
            (TaskState.RUNNING, TaskState.OBSERVING),
            (TaskState.OBSERVING, TaskState.VERIFYING),
        ):
            self._assert_allowed(current, target)


if __name__ == "__main__":
    unittest.main()
