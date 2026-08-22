"""Session-scoped history ring for the TUI prompt.

Keeps the list of submitted prompts so the user can arrow-up through prior
inputs (readline history semantics).  Pure Python, no dependencies, so it
works identically under textual or the plain ``input()`` fallback.
"""

from __future__ import annotations

import os
import json
from pathlib import Path
from typing import List, Optional


class PromptHistory:
    """An in-memory ring with optional persistence to a JSON file.

    Auto-persists on append if a ``path`` is supplied, and loads any prior
    entries at construction.  This gives cross-run continuity: resume a
    session and your earlier prompts are still arrow-navigable.
    """

    def __init__(self, path: Optional[Path] = None, max_entries: int = 200) -> None:
        self.path = Path(path) if path else None
        self.max_entries = max_entries
        self._entries: List[str] = []
        if self.path and self.path.exists():
            self._load()

    # ── persistence ───────────────────────────────────────────
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._entries = [str(e) for e in data.get("prompts", [])][:]
        except (OSError, ValueError, json.JSONDecodeError):
            self._entries = []

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"prompts": self._entries[-self.max_entries:]}),
                encoding="utf-8",
            )
        except OSError:
            pass  # non-fatal; history persistence is best-effort

    # ── ring API (matches readline's history_vector expectations) ──
    def add(self, entry: str) -> None:
        entry = (entry or "").strip()
        if not entry:
            return
        # Don't add if identical to the most recent entry.
        if self._entries and self._entries[-1] == entry:
            return
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]
        self._save()

    @property
    def entries(self) -> List[str]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, index: int) -> str:
        return self._entries[index]
