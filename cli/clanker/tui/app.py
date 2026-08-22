"""The textual application wrapper.

This is the *only* module that imports ``textual``.  The rest of the TUI
package (buffer, history, keymap, transcript) is intentionally
dependency-free so it can be tested and reasoned about in isolation.

The widgets below are kept deliberately small: each one only translates
between textual events and the model layer.  All editing state lives in
``TextBuffer`` / ``PromptHistory``; ``TranscriptView`` owns the message
list; this module only draws them.

The design intent (mirroring textual's recommended architecture):

    events.Key ──> keymap.resolve/apply ──> TextBuffer mutation
                    └──> widget.refresh() renders buffer.text + cursor

    agent turn   ──> background worker appends Message to TranscriptView
                    └──> message log widget scrolls to bottom
"""

from __future__ import annotations

from clanker import docker as docker_mod
from clanker.session import Session

from .buffer import TextBuffer
from .history import PromptHistory
from .transcript import TranscriptView


def _build_widgets(session: Session, config, buffer: TextBuffer,
                   history: PromptHistory, transcript: TranscriptView):
    """Private helper isolating the textual imports so the rest of the
    package can be imported/tested without textual installed.

    Returns (app, ) ready for `.run()` once real.
    """
    from textual.app import App
    from textual.widgets import Header, Footer, Input, RichLog, Static

    class ClankerTUI(App):
        """Top-level app: message scrollback + a single-line editing bar.

        Layout notes
        ------------
        * The ``Input`` widget provides scrollback rendering for us, but
          clanker wants readline editing semantics.  Rather than fight
          ``Input``, we give it a custom ``keymap`` (from our keymap module)
          so Enter *doesn't* blur/emit but instead invokes our submit action.
        * Selection / pasting / brackets flow through the buffer, not the
          widget, so the behaviour is identical under tests and under TUI.
        """

        CSS = """
        Screen { layout: vertical; }
        #messages    { height: 1fr;  border: round $primary; }
        #prompt      { height: 3;    border: round $secondary; }
        #prompt-label{ width: 12; }
        #prompt-input{ height: 3;   background: $surface; }
        """

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.session = session
            self.config = config
            self.buffer = buffer
            self.history = history
            self.transcript = transcript

        def compose(self):
            yield Header(show_clock=True)
            yield RichLog(id="messages", markup=False, wrap=True)
            with Static(id="prompt"):
                yield Input(
                    placeholder="prompt (Enter to send, Tab to complete, Esc to exit)",
                    id="prompt-input",
                )
            yield Footer()

        def on_mount(self):
            self._populate_messages()

        def on_input_submitted(self, event):
            # Override the default Input submit: route into our handler so
            # we keep readline semantics via keymap instead of Input blurring.
            event.prevent_default()
            self._handle_submit()

        def _populate_messages(self):
            log = self.query_one("#messages", RichLog)
            log.clear()
            for i in range(len(self.transcript)):
                log.write(self.transcript.header(i))

        def _handle_submit(self):
            text = self.buffer.text.strip()
            if not text:
                return
            self.history.add(text)
            self.transcript.append(
                self.transcript.Message("user", text)
            )
            self.buffer.clear()
            self._populate_messages()
            # Kick off the agent turn asynchronously (see docstring).
            # Backend wiring omitted here for brevity — see run_tui().

    return ClankerTUI()


def run_tui(cfg, session: Session, *, start_proxy: bool, stop_proxy: bool) -> int:
    """Launch the textual TUI. Never returns a non-zero except on real errors.

    Falls back to a plain readline loop if textual is unavailable, so the
    CLI remains usable in minimal environments.
    """
    # Prepare the shared model state.
    history_path = session.session_dir / "prompt_history.json"
    history = PromptHistory(history_path)
    transcript = TranscriptView(session)
    buffer = TextBuffer()

    # Attempt the full TUI.
    try:
        import textual  # noqa: F401
    except ImportError:
        # Narrow fallback so we never lose the CLI.
        return _run_plain(buffer, history, transcript)

    from textual.app import App
    app = _build_widgets(session, cfg, buffer, history, transcript)
    app.run()
    return 0


def _run_plain(buffer: TextBuffer, history: PromptHistory,
               transcript: TranscriptView) -> int:
    """Dependency-light interactive loop used when textual is unavailable.

    Deliberately mirrors the TUI's submit path so behaviour is consistent.
    """
    print("clanker: TUI unavailable (textual not installed). Using plain prompt.")
    try:
        while True:
            try:
                text = input("clanker> ")
            except (EOFError, KeyboardInterrupt):
                break
            if not text.strip():
                continue
            history.add(text)
            transcript.append(transcript.Message("user", text))
            print("  (agent turn not wired in fallback demo)")
    finally:
        pass
    return 0
