"""TermuxRuntimeAdapter (Phase 12): RuntimePort with durable sessions.

Sessions live under .pi/sessions/<sessionId>.json behind the standard
SHA-256 integrity envelope, so start/stop/resume survive process restarts
and Termux backgrounding - the foundation for background execution. The
adapter binds a ModelPort; send() translates its response into normalized
RuntimeEvents. No authority: every tool call still flows through the Core
pipeline.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import List, Optional, Union

from continuity.task_store import (
    TaskIntegrityError,
    _atomic_write_json,
    _digest,
    _load_envelope,
)
from core import ModelRequest, RuntimeEvent, RuntimeSession, utcnow_iso
from core.enums import ModelRole
from runtimes import RuntimePort


class TermuxRuntimeAdapter(RuntimePort):
    """Durable-session RuntimePort implementation for Termux."""

    def __init__(self, root: Union[str, Path], port=None, role=ModelRole.GENERAL_AGENT):
        self.root = Path(root)
        self.port = port
        self.role = role

    def _sessions_dir(self) -> Path:
        return self.root / "sessions"

    def _session_path(self, session_id: str) -> Path:
        return self._sessions_dir() / f"{session_id}.json"

    def start(self, task_context: dict) -> RuntimeSession:
        session = RuntimeSession(sessionId=uuid.uuid4().hex,
                                 taskContext=dict(task_context), state="RUNNING")
        self._save(session)
        return session

    def send(self, session_id: str, input=None) -> List[RuntimeEvent]:
        session = self._load(session_id)
        events: List[RuntimeEvent] = []
        if self.port is not None:
            request = ModelRequest(
                role=self.role,
                messages=[{"role": "runtime", "content": str(input or "")}],
                context={"taskId": session.taskContext.get("taskId")},
            )
            response = self.port.generate(request)
            events.append(RuntimeEvent(type="tool_calls", payload={
                "toolCalls": [c.to_dict() for c in response.toolCalls],
                "finishReason": response.finishReason,
                "usage": response.usage,
            }, timestamp=utcnow_iso()))
        return events

    def stop(self, session_id: str) -> dict:
        session = self._load(session_id)
        session.state = "STOPPED"
        self._save(session)
        return {"ok": True}

    def resume(self, session_id: str) -> RuntimeSession:
        session = self._load(session_id)
        session.state = "RUNNING"
        self._save(session)
        return session

    def storage_usage(self) -> dict:
        """Local storage accounting for the durable state root."""
        import shutil
        usage = shutil.disk_usage(str(self.root))
        total = sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file())
        return {"bytesUsed": total, "bytesTotal": usage.total,
                "bytesFree": usage.free}

    # -- durable session persistence -------------------------------------------

    def _save(self, session: RuntimeSession) -> None:
        payload = session.to_dict()
        envelope = {"payload": payload,
                    "integrity": {"algorithm": "sha256", "digest": _digest(payload)}}
        path = self._session_path(session.sessionId)
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(path, envelope)

    def _load(self, session_id: str) -> RuntimeSession:
        path = self._session_path(session_id)
        if not path.is_file():
            raise KeyError(f"unknown session {session_id}")
        return RuntimeSession.from_dict(_load_envelope(path))
