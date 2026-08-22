"""A readline-style editing buffer, decoupled from any rendering backend.

Why this module exists
----------------------
The goal of the TUI is to bring an interactive, readline-flavoured prompt
onto the *host* CLI instead of delegating to a shell inside the container
(which is what ``docker run -it ... /bin/bash`` does today).

The editing behaviour below is the "model" half of that experience.  It is
pure Python with zero dependencies, so it can be unit-tested in this
sandbox, reused by multiple front-ends (a textual widget, a plain
``input()`` fallback, a headless test harness), and kept free of the
rendering/scheduling complexity that lives in ``textual``.

The class mirrors the *editing* subset of GNU readline's keymap:

    C-a/C-e   start/end of line      C-w   kill previous word
    C-b/C-f   back/forward char      C-u   kill to start of line
    C-k       kill to end of line    C-y   yank (paste) last kill

Documented operations map cleanly onto textual ``Key`` events, so the app
widget is only responsible for turning key events into these calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence


# ─── Completion support ──────────────────────────────────────
Completer = Callable[[str], Sequence[str]]


# ─── A kill is a removed chunk we can yank back ───────────────
@dataclass
class Kill:
    text: str
    position: int  # where it was removed from


# ─── Undo entry ───────────────────────────────────────────────
@dataclass
class UndoEntry:
    text: str
    cursor: int


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


class TextBuffer:
    """A model of a single line being edited, with a cursor.

    The buffer holds the current text, the cursor offset, an undo stack
    and a kill-ring.  Read-only consumers observe text+cursor via
    properties ``text`` and ``cursor``; the caller (keymap/widget) decides
    rendering.
    """

    def __init__(self, initial: str = "", completer: Optional[Completer] = None) -> None:
        self._text = list(initial)
        self.cursor = len(self._text)  # 0 .. len(text)
        self._undo: List[UndoEntry] = []
        self._kill_ring: List[Kill] = []
        self.completer: Optional[Completer] = completer

    # ── read-only view ────────────────────────────────────────
    @property
    def text(self) -> str:
        return "".join(self._text)

    @property
    def length(self) -> int:
        return len(self._text)

    @property
    def at_start(self) -> bool:
        return self.cursor == 0

    @property
    def at_end(self) -> bool:
        return self.cursor == len(self._text)

    # ── undo bookkeeping ──────────────────────────────────────
    def _snapshot(self) -> None:
        self._undo.append(UndoEntry("".join(self._text), self.cursor))
        if len(self._undo) > 1024:
            self._undo.pop(0)

    # ── cursor movement ───────────────────────────────────────
    def move_left(self, n: int = 1) -> bool:
        prev = self.cursor
        self.cursor = max(0, self.cursor - n)
        return self.cursor != prev

    def move_right(self, n: int = 1) -> bool:
        prev = self.cursor
        self.cursor = min(len(self._text), self.cursor + n)
        return self.cursor != prev

    def move_to_start(self) -> bool:
        return self.move_left(self.cursor)

    def move_to_end(self) -> bool:
        return self.move_right(len(self._text) - self.cursor)

    # ── editing ───────────────────────────────────────────────
    def insert(self, ch: str) -> None:
        if ch == "":
            return
        self._snapshot()
        self._text[self.cursor:self.cursor] = list(ch)
        self.cursor += len(ch)

    def delete_before(self) -> bool:
        """Backspace: remove the char to the left of the cursor."""
        if self.cursor == 0:
            return False
        self._snapshot()
        del self._text[self.cursor - 1]
        self.cursor -= 1
        return True

    def delete_after(self) -> bool:
        """Delete: remove the char at the cursor."""
        if self.cursor >= len(self._text):
            return False
        self._snapshot()
        del self._text[self.cursor]
        return True

    def kill_to_start(self) -> Optional[str]:
        """C-u — remove [0, cursor), stash it in the kill ring."""
        removed = "".join(self._text[: self.cursor])
        if not removed:
            return None
        self._kill_ring.append(Kill(removed, 0))
        self._snapshot()
        del self._text[: self.cursor]
        self.cursor = 0
        return removed

    def kill_to_end(self) -> Optional[str]:
        """C-k — remove [cursor, end)."""
        removed = "".join(self._text[self.cursor :])
        if not removed:
            return None
        self._kill_ring.append(Kill(removed, self.cursor))
        self._snapshot()
        del self._text[self.cursor :]
        return removed

    def kill_word_before(self) -> Optional[str]:
        """C-w — remove the word to the left of the cursor."""
        start = self.cursor
        while start > 0 and self._text[start - 1] == " ":
            start -= 1
        while start > 0 and _is_word_char(self._text[start - 1]):
            start -= 1
        if start == self.cursor:
            return None
        removed = "".join(self._text[start : self.cursor])
        self._kill_ring.append(Kill(removed, start))
        self._snapshot()
        del self._text[start : self.cursor]
        self.cursor = start
        return removed

    def yank(self) -> bool:
        """C-y — paste the most recent kill at the cursor."""
        if not self._kill_ring:
            return False
        last = self._kill_ring[-1]
        self._snapshot()
        self._text[self.cursor : self.cursor] = list(last.text)
        self.cursor += len(last.text)
        return True

    def undo(self) -> bool:
        """Revert to the previous snapshot."""
        if not self._undo:
            return False
        prev = self._undo.pop()
        self._text = list(prev.text)
        self.cursor = prev.cursor
        return True

    # ── completion ────────────────────────────────────────────
    def completions(self) -> List[str]:
        """Return candidate completions for the current prefix."""
        if not self.completer:
            return []
        return self.completer(self.text)

    def replace(self, new_text: str) -> None:
        """Replace the whole line (used by accept/confirm)."""
        self._snapshot()
        self._text = list(new_text)
        self.cursor = len(new_text)

    def clear(self) -> None:
        self.replace("")

    def __str__(self) -> str:
        pos = "|" if self.cursor == len(self._text) else " "
        return "".join(self._text[: self.cursor]) + pos + "".join(self._text[self.cursor :])


# Convenience: a common default completer for file/command-ish tokens.
_PATH_TOKEN = re.compile(r"([^\s]+)$")


def path_token_completer(candidates: Sequence[str]) -> Completer:
    """Closure that completes the last whitespace-separated token."""

    def complete(prefix: str) -> List[str]:
        m = _PATH_TOKEN.search(prefix)
        token = m.group(1) if m else prefix
        tok_start = (prefix.rfind(" ") + 1) if " " in prefix else 0
        base = prefix[:tok_start]
        matches = [base + c for c in candidates if c.startswith(token)]
        return sorted(matches, key=lambda s: (s != prefix, s))

    return complete
