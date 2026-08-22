"""Model layer bridging a clanker ``Session`` transcript to the TUI.

Renders the JSONL ``transcript.jsonl`` into a scrollable, indexable list of
structured ``Message`` records, instead of the flat ``Session.render_text()``
string used by ``clanker log``.  Keeping this as a pure model (no textual)
means we can unit-test the grouping/rendering logic and let the textual
widget just draw the records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional

from clanker.session import Session


@dataclass
class Message:
    role: str
    content: str
    timestamp: Optional[str]
    tool_calls: List[Dict[str, object]] = None

    def __init__(self, role, content, timestamp=None, tool_calls=None):
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.tool_calls = tool_calls or []

    def tool_names(self) -> List[str]:
        return [
            tc.get("function", {}).get("name", "unknown")
            for tc in self.tool_calls
        ]


class TranscriptView:
    """An indexable, ordered list of messages derived from a Session."""

    def __init__(self, session: Optional[Session] = None) -> None:
        self._messages: List[Message] = []
        if session is not None:
            self.load(session)

    def load(self, session: Session) -> None:
        self._messages = []
        for event in session.read_events():
            tool_calls = event.get("tool_calls") or []
            if event.get("content") or tool_calls:
                self._messages.append(
                    Message(
                        role=event.get("role", "unknown"),
                        content=event.get("content", ""),
                        timestamp=event.get("timestamp"),
                        tool_calls=tool_calls,
                    )
                )

    def append(self, message: Message) -> None:
        self._messages.append(message)

    # ── collection API (mirrors list semantics for widgets) ───
    def __len__(self) -> int:
        return len(self._messages)

    def __getitem__(self, index):
        return self._messages[index]

    def __iter__(self):
        return iter(self._messages)

    @property
    def messages(self) -> List[Message]:
        return self._messages

    # ── rendering helpers for a widget ────────────────────────
    def header(self, index: int) -> str:
        """A short one-line summary of a message for the list."""
        msg = self._messages[index]
        label = {"user": "you", "assistant": "assistant",
                 "system": "system", "tool": "tool"}.get(msg.role, msg.role)
        tools = ", ".join(msg.tool_names())
        first_line = msg.content.strip().splitlines()[0] if msg.content.strip() else ""
        detail = f" {first_line[:60]}"
        tools_suffix = f"  [tools: {tools}]" if tools else ""
        return f"{label:<10}{detail}{tools_suffix}"
