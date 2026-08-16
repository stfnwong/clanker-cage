# session.py
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional
from datetime import datetime, timezone


class Session:
    """
    A clanker session: a directory containing metadata and a JSONL transcript.

    Layout:
        sessions_root/
            20260816-143022-3f2a/
                metadata.json
                transcript.jsonl
    """

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir).resolve()
        self.session_id = self.session_dir.name
        self.metadata_path = self.session_dir / "metadata.json"
        self.transcript_path = self.session_dir / "transcript.jsonl"
        self.metadata: Dict[str, Any] = {}

        # Load metadata if it exists
        if self.metadata_path.exists():
            with self.metadata_path.open("r", encoding="utf-8") as f:
                self.metadata = json.load(f)

    # ─── Creation ────────────────────────────────────────────
    @classmethod
    def create(cls, sessions_root: Path, project_root: Path, **kwargs) -> "Session":
        """
        Create a new session with a unique ID and initial metadata.

        Args:
            sessions_root: Parent directory for all sessions.
            project_root: Path to the project being worked on.
            **kwargs: Additional metadata fields (model, provider, etc.)
        """
        sessions_root = Path(sessions_root).expanduser()
        sessions_root.mkdir(parents=True, exist_ok=True)

        session_id = cls._generate_id()
        session_dir = sessions_root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)

        session = cls(session_dir)
        session.metadata = {
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_root": str(Path(project_root).resolve()),
            "project_name": Path(project_root).name,
            "model": kwargs.get("model", "deepseek-chat"),
            "provider": kwargs.get("provider", "deepseek"),
            "status": "active",
        }
        # Merge extra kwargs
        for key, value in kwargs.items():
            if key not in session.metadata:
                session.metadata[key] = value

        session._save_metadata()
        return session

    @classmethod
    def from_id(cls, sessions_root: Path, session_id: str) -> "Session":
        """Load an existing session by ID."""
        session_dir = Path(sessions_root).expanduser() / session_id
        if not session_dir.is_dir():
            raise FileNotFoundError(f"Session {session_id} not found in {sessions_root}")
        return cls(session_dir)

    @staticmethod
    def _generate_id() -> str:
        """Generate a sortable session ID: YYYYMMDD-HHMMSS-shortuuid."""
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        short_hash = uuid.uuid4().hex[:4]
        return f"{timestamp}-{short_hash}"

    def _save_metadata(self) -> None:
        """Write metadata.json atomically."""
        tmp = self.metadata_path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.metadata, f, indent=2, default=str)
        tmp.replace(self.metadata_path)

    # ─── Directory Management ────────────────────────────────
    def mkdir(self) -> None:
        """Ensure the session directory exists (for mounting into container)."""
        self.session_dir.mkdir(parents=True, exist_ok=True)

    # ─── Transcript Logging ─────────────────────────────────
    def append(self, event: Dict[str, Any]) -> None:
        """
        Append an event to the transcript.

        The event should include at least 'role' and 'content'.
        Automatically adds a timestamp if not present.
        """
        self.mkdir()
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def read_events(self) -> List[Dict[str, Any]]:
        """Read all transcript events into a list."""
        if not self.transcript_path.exists():
            return []
        events = []
        with self.transcript_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        events.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Skip malformed lines (shouldn't happen)
                        continue
        return events

    def iter_events(self) -> Iterator[Dict[str, Any]]:
        """Lazily iterate over transcript events."""
        if not self.transcript_path.exists():
            return
        with self.transcript_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        continue

    # ─── Status Updates ─────────────────────────────────────
    def mark_finished(self, exit_code: int = 0) -> None:
        """Mark the session as finished."""
        self.metadata["status"] = "finished"
        self.metadata["ended_at"] = datetime.now(timezone.utc).isoformat()
        self.metadata["exit_code"] = exit_code
        self._save_metadata()

    def mark_failed(self, error: str) -> None:
        """Mark the session as failed."""
        self.metadata["status"] = "failed"
        self.metadata["ended_at"] = datetime.now(timezone.utc).isoformat()
        self.metadata["error"] = error
        self._save_metadata()

    # ─── Helpers for Log Viewing ─────────────────────────────
    def render_text(self) -> str:
        """Return a human-readable transcript (for 'clanker log')."""
        lines = []
        for event in self.read_events():
            role = event.get("role", "unknown")
            content = event.get("content", "")
            timestamp = event.get("timestamp", "")
            # Truncate very long lines for display
            if isinstance(content, str) and len(content) > 2000:
                content = content[:2000] + "\n... [truncated]"
            lines.append(f"[{timestamp}] {role.upper()}: {content}")
            # Handle tool calls if present
            tool_calls = event.get("tool_calls", [])
            for tc in tool_calls:
                name = tc.get("function", {}).get("name", "unknown")
                args = tc.get("function", {}).get("arguments", "")
                lines.append(f"  [tool_call] {name}({args})")
        return "\n".join(lines)
