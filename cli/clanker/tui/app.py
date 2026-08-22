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

Wiring contract
---------------
``run_tui`` is the host CLI's entrypoint for the *interactive* prompting
experience.  The **text buffer that the user fills in is supplied here by
textual** (the ``Input`` widget + ``TextBuffer`` model), replacing the
bash shell that ``docker run -it ... /bin/bash`` used to provide.

Each submitted line is handed to an injected ``run_agent(text)`` callback
(by default a one-shot ``DockerManager.run(session, initial_prompt=...)``
call; a persistent agent-loop can be plugged in later).  The callback runs
on a background worker so the UI stays live; when it returns we re-load
the session's ``transcript.jsonl`` into ``TranscriptView`` and refresh the
scrollback with the assistant's reply.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from .buffer import TextBuffer
from .history import PromptHistory
from .transcript import TranscriptView

# Optional backend callback.  ``run_tui`` supplies this; frontends/tests may
# pass a different one.  Signature: run_agent(text: str) -> None
RunAgent = Callable[[str], None]


def _build_widgets(session, config, buffer: TextBuffer,
                   history: PromptHistory, transcript: TranscriptView,
                   run_agent: Optional[RunAgent] = None):
    """Private helper isolating the textual imports so the rest of the
    package can be imported/tested without textual installed.

    Returns (app, ) ready for ``.run()`` once real.
    """
    # TODO: this is now in the env, move to top?
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
            # Backend that actually runs the agent for a submitted line.
            self.run_agent = run_agent

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

        # ── transcript rendering ─────────────────────────────
        def _populate_messages(self):
            log = self.query_one("#messages", RichLog)
            log.clear()
            for i in range(len(self.transcript)):
                log.write(self.transcript.header(i))

        def _reload_transcript(self):
            """Re-read transcript.jsonl so new agent turns appear."""
            if self.transcript is not None and self.session is not None:
                self.transcript.load(self.session)
            self._populate_messages()

        # ── submit path ───────────────────────────────────────
        def _handle_submit(self):
            text = self.buffer.text.strip()
            if not text:
                return
            self.history.add(text)
            self.transcript.append(self.transcript.Message("user", text))
            self._populate_messages()
            self.buffer.clear()
            inp = self.query_one("#prompt-input", Input)
            inp.value = ""

            if self.run_agent is None:
                # No backend wired (e.g. pure widget test) — nothing more.
                return

            # Run the agent turn on a background worker so the TUI stays
            # live and editable while the turn is in flight.
            self.run_worker(
                self._agent_worker,
                text,
                name="clanker-agent-turn",
                group="agent",
                exclusive=False,
            )

        def _agent_worker(self, text: str) -> None:
            """Runs on a worker thread; updates the transcript when done."""
            try:
                if self.run_agent is not None:
                    self.run_agent(text)
            finally:
                # Refresh happens back on the UI thread.
                self.call_from_thread(self._reload_transcript)

    return ClankerTUI()


def run_tui(cfg, session, *, start_proxy: bool, stop_proxy: bool,
            run_agent: Optional[RunAgent] = None) -> int:
    """Launch the textual TUI that *supplies the text buffer*.

    This is the replacement interactive front-end: instead of dropping the
    user into a bash prompt owned by the container, clanker presents a
    textual editing bar whose text is captured by ``TextBuffer``.  Each
    submitted line is forwarded to ``run_agent`` (a one-shot
    ``DockerManager.run(session, initial_prompt=...)`` by default), and the
    assistant's reply is read back from ``transcript.jsonl``.

    Falls back to a plain readline loop if textual is unavailable, so the
    CLI remains usable in minimal environments.

    Returns a process exit code.
    """
    # Prepare the shared model state.
    history_path = session.session_dir / "prompt_history.json"
    history = PromptHistory(history_path)
    transcript = TranscriptView(session)
    buffer = TextBuffer()

    # If the caller did not supply a backend, default to a one-shot agent
    # run that reuses the same session (so every turn lands in the same
    # transcript.jsonl).  ``import`` here keeps docker a lazy dependency
    # and avoids an import cycle with cli.py.
    if run_agent is None:
        from clanker import docker as docker_mod

        manager = docker_mod.DockerManager(cfg)
        # Ensure the session dir exists before the container writes to it.
        session.mkdir()

        def _one_shot_run(text: str) -> None:
            # Mirrors the `prompt` command path: run the agent-loop
            # one-shot with this prompt, reusing the same session.
            manager.run(session, initial_prompt=text)

        run_agent = _one_shot_run

    # Attempt the full TUI.
    try:
        import textual  # noqa: F401
    except ImportError:
        # Narrow fallback so we never lose the CLI.
        return _run_plain(buffer, history, transcript, session, run_agent)

    app = _build_widgets(session, cfg, buffer, history, transcript,
                         run_agent=run_agent)
    app.run()
    return 0


def _run_plain(buffer: TextBuffer, history: PromptHistory,
               transcript: TranscriptView, session,
               run_agent: Optional[RunAgent] = None) -> int:
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
            if run_agent is not None:
                run_agent(text)
                transcript.load(session)  # bring the assistant turn into view
                for msg in transcript:
                    print(f"  {msg.role}: {msg.content}")
    finally:
        pass
    return 0
