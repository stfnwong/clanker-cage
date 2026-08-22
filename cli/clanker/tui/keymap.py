"""Map textual ``Key`` events to editing actions on a ``TextBuffer``.

The widget layer (app.py) will convert an incoming ``events.Key`` into one
of the action strings here.  Keeping the mapping table in plain data means
it can be unit-tested (and later made user-configurable) without a running
terminal.

Actions that "happen" (submit, tab, escape) return sentinel strings so the
keymap layer can own control flow while the widget just reacts.
"""

from __future__ import annotations

from typing import Optional

from .buffer import TextBuffer

# Reserved action names the widget must handle for control flow.
SUBMIT = "submit"
COMPLETE = "complete"
CANCEL = "cancel"

# A plain mapping from textual key.name -> action string.
# `char` is handled separately (typing) in the widget.
KEYMAP = {
    "left": "move_left",
    "right": "move_right",
    "home": "move_to_start",
    "end": "move_to_end",
    "backspace": "delete_before",
    "delete": "delete_after",
    "ctrl+a": "move_to_start",
    "ctrl+e": "move_to_end",
    "ctrl+b": "move_left",
    "ctrl+f": "move_right",
    "ctrl+k": "kill_to_end",
    "ctrl+u": "kill_to_start",
    "ctrl+w": "kill_word_before",
    "ctrl+y": "yank",
    "ctrl+underscore": "undo",
    "enter": SUBMIT,
    "tab": COMPLETE,
    "escape": CANCEL,
}


def resolve(key_name: str, char: Optional[str] = None) -> str:
    """Return an action name for a key event.

    Prefers the explicit keymap; otherwise falls back to inserting the
    character that was typed (printable characters).
    """
    action = KEYMAP.get(key_name)
    if action is not None:
        return action
    if char and char.isprintable():
        return "insert"
    return "ignore"


def apply(buffer: TextBuffer, action: str, char: Optional[str] = None) -> bool:
    """Apply a resolved action to a buffer. Returns True if the buffer changed."""
    handler = getattr(buffer, action, None)
    if handler is not None and callable(handler):
        if action == "insert" and char is not None:
            buffer.insert(char)
            return True
        return bool(handler())
    # Complete uses the buffer's completer via a separate call.
    return False
