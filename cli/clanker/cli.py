"""clanker — the CLI entrypoint.

This is the python replacement for the original bash ``clanker`` script.
It owns the full lifecycle of a session:

    preflight -> proxy management -> session creation -> docker launch -> cleanup

The command surface intentionally mirrors what the bash script could do,
but with durable, resumable sessions on top:

    clanker                       drop into a shell inside the container
    clanker run [--no-proxy] [--stop-proxy]
                                  explicit form of the default launch
    clanker prompt <text>         one-shot agent run, exits after answering
    clanker resume <session-id>   continue a prior session
    clanker log <session-id>      render a transcript
    clanker sessions              list known sessions
    clanker status                health + config summary
    clanker stop                  stop the provider proxy
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from clanker import docker as docker_mod
from clanker.config import (
    Config, 
    get_platform, 
    DEFAULT_MODEL,
    DEFAULT_PROVIDER
)
from clanker.platform import ProxyManager
from clanker.session import Session


# ─── Builders ─────────────────────────────────────────────────
def _build_config(
    project: str, 
    cache_dir: str | None = None,
    secrets_dir: str | None = None, 
    model: str=DEFAULT_MODEL,
    provider: str=DEFAULT_PROVIDER
) -> Config:
    """Construct a Config from CLI inputs + environment defaults."""
    return Config(
        platform=get_platform(),
        project_root=Path(project),
        cache_dir=Path(cache_dir) if cache_dir else None,
        secrets_dir=Path(secrets_dir) if secrets_dir else None,
        model=model,
        provider=provider,
    )


def _print_banner(cfg: Config, session: Session, proxy: ProxyManager,
                  booted_proxy: bool) -> None:
    """Render the launch banner (mirrors the bash script's box)."""
    box = "╔" + "═" * 56 + "╗"
    line = "║"
    def row(label: str, value: str) -> str:
        pad = " " * (55 - len(label) - len(value))
        return f"║  {label}{pad} {value}║"
    print(box)
    print(row("clanker", "agent environment"))
    print("╠" + "═" * 56 + "╣")
    print(row("Project:", str(cfg.project_root)))
    print(row("Provider:", cfg.provider or "[No provider]"))
    print(row("Model:", cfg.model or "[No model]"))
    print(row("Mode:", cfg.provider_mode))
    print(row("Session:", session.session_id))
    print(row("Container:", f"clanker-{session.session_id}"))
    print(row("Proxy:", "booted" if booted_proxy else "already up" if proxy.health_check() else "offline"))
    print(box)


# ─── Proxy resolution (one shared sequence) ──────────────────
def _resolve_proxy(cfg: Config, *, start: bool):
    """Ensure the provider proxy is up (if requested) and return a ProxyManager.

    Returns (proxy, booted) where ``booted`` records whether *we* started it,
    so the caller knows whether cleanup should stop it.
    """
    proxy = ProxyManager(cfg)
    booted = False

    if not start:
        # offline mode — no proxy, no socket mount.
        cfg.provider_mode = "offline"
        cfg.provider_endpoint = ""
        cfg.provider_socket_host = None
        return proxy, False

    if proxy.health_check():
        return proxy, False

    print("  Proxy not running. Starting service...")
    try:
        proxy.start()
    except Exception as exc:  # pragma: no cover - platform-specific
        click.echo(f"  WARNING: could not start proxy ({exc}). Running offline.", err=True)
        cfg.provider_mode = "offline"
        cfg.provider_endpoint = ""
        cfg.provider_socket_host = None
        return proxy, False

    if not proxy.wait_for_health():
        click.echo("  WARNING: proxy failed to become healthy. Running offline.", err=True)
        cfg.provider_mode = "offline"
        cfg.provider_endpoint = ""
        cfg.provider_socket_host = None
        return proxy, False

    booted = True
    return proxy, booted


# ─── Shared launch sequence ───────────────────────────────────
def _launch(cfg: Config, *, start_proxy: bool, stop_proxy: bool,
            session: Session, initial_prompt: str | None = None) -> int:
    """Run the container for a (new or resumed) session. Returns exit code."""
    proxy, booted = _resolve_proxy(cfg, start=start_proxy)

    cfg.ensure_dirs()
    session.mkdir()

    _print_banner(cfg, session, proxy, booted)

    manager = docker_mod.DockerManager(cfg)
    try:
        code = manager.run(session, initial_prompt=initial_prompt)
        if code == 0:
            session.mark_finished()
        else:
            session.mark_failed(f"exit code {code}")
        return code
    except Exception as exc:
        session.mark_failed(str(exc))
        click.echo(f"error: {exc}", err=True)
        return 1
    finally:
        if stop_proxy:
            proxy.stop()


# ─── Editor prompt helper ─────────────────────────────────────
def _open_editor_and_get_text() -> Optional[str]:
    import subprocess
    import tempfile
    import os
    editor = os.environ.get("EDITOR", "vi")
    with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as f:
        path = f.name
    try:
        subprocess.run([editor, path], check=True)
        return Path(path).read_text()
    except Exception:
        return None
    finally:
        Path(path).unlink(missing_ok=True)


def _collect_initial_prompt(prompt: str | None, pipe: bool, editor: bool) -> str | None:
    """Resolve the various ways of supplying an initial agent prompt."""
    if pipe:
        return sys.stdin.read()
    if prompt:
        return prompt
    if editor:
        return _open_editor_and_get_text()
    return None


# ─── command group ────────────────────────────────────────────
@click.group()
@click.version_option(package_name="clanker-cli", prog_name="clanker")
def cli() -> None:
    """clanker — a sandboxed agent environment."""


# ─── default: clanker (no subcommand) drops you into a shell ──
@cli.command(context_settings=dict(ignore_unknown_options=True))
@click.option("--project", "-p", default=".", show_default=True,
              help="Project root to mount as /workspace.")
@click.option("--no-proxy", is_flag=True, help="Run offline (no provider proxy).")
@click.option("--stop-proxy", is_flag=True, help="Stop the proxy after the session.")
@click.option("--model", default=None, help="Override the default model.")
@click.option("--provider", default=None, help="Override the default provider.")
def run(project: str, no_proxy: bool, stop_proxy: bool,
        model: Optional[str], provider: Optional[str]) -> int:
    """Start a new interactive clanker session (default)."""
    cfg = _build_config(
        project, 
        model=model or DEFAULT_MODEL, 
        provider=provider or DEFAULT_PROVIDER
    )
    session = Session.create(cfg.sessions_root, cfg.project_root,
                             model=cfg.model, provider=cfg.provider)
    return _launch(cfg, start_proxy=not no_proxy, stop_proxy=stop_proxy,
                   session=session)


# ─── one-shot prompt ──────────────────────────────────────────
@cli.command()
@click.option("--project", "-p", default=".", show_default=True)
@click.option("--no-proxy", is_flag=True)
@click.option("--pipe", is_flag=True, help="Read context from stdin.")
@click.option("-e", "--editor", is_flag=True, help="Compose the prompt in $EDITOR.")
@click.option("--model", default=None)
@click.option("--provider", default=None)
@click.argument("prompt", nargs=-1)
def prompt(project: str, no_proxy: bool, pipe: bool, editor: bool,
           model: Optional[str], provider: Optional[str],
           prompt: tuple) -> int:
    """Run the agent for a single prompt, then exit."""
    text = _collect_initial_prompt(" ".join(prompt) if prompt else None,
                                   pipe, editor)
    if not text or not text.strip():
        click.echo("prompt: no text given (use --pipe, an argument, or -e)", err=True)
        return 2

    cfg = _build_config(
        project, 
        model=model or DEFAULT_MODEL, 
        provider=provider or DEFAULT_PROVIDER
    )
    session = Session.create(cfg.sessions_root, cfg.project_root,
                             model=cfg.model, provider=cfg.provider)
    return _launch(cfg, start_proxy=not no_proxy, stop_proxy=False,
                   session=session, initial_prompt=text.strip())


# ─── resume ───────────────────────────────────────────────────
@cli.command()
@click.option("--project", "-p", default=".", show_default=True)
@click.option("--no-proxy", is_flag=True)
@click.option("--stop-proxy", is_flag=True)
@click.argument("session_id")
def resume(project: str, no_proxy: bool, stop_proxy: bool, session_id: str) -> int:
    """Resume an existing session."""
    cfg = _build_config(project)
    try:
        session = Session.from_id(cfg.sessions_root, session_id)
    except FileNotFoundError:
        click.echo(f"No session '{session_id}' under {cfg.sessions_root}", err=True)
        # Offer a hint to the user.
        click.echo("Run 'clanker sessions' to list them.", err=True)
        return 1
    if session.metadata.get("status") == "finished":
        click.echo(f"Session {session_id} already finished.", err=True)
        return 1
    return _launch(cfg, start_proxy=not no_proxy, stop_proxy=stop_proxy,
                   session=session)


# ─── log ──────────────────────────────────────────────────────
@cli.command()
@click.option("--project", "-p", default=".", show_default=True)
@click.argument("session_id")
def log(project: str, session_id: str) -> int:
    """Render a session transcript to stdout."""
    cfg = _build_config(project)
    try:
        session = Session.from_id(cfg.sessions_root, session_id)
    except FileNotFoundError:
        click.echo(f"No session '{session_id}' under {cfg.sessions_root}", err=True)
        return 1
    click.echo(session.render_text())
    return 0


# ─── sessions ─────────────────────────────────────────────────
@cli.command()
@click.option("--project", "-p", default=".", show_default=True)
def sessions(project: str) -> int:
    """List sessions (newest first), with status and timestamps."""
    cfg = _build_config(project)
    root = cfg.sessions_root
    if not root.is_dir():
        click.echo("No sessions yet.")
        return 0
    entries = sorted(root.glob("*/metadata.json"), key=lambda p: p.name, reverse=True)
    if not entries:
        click.echo("No sessions yet.")
        return 0
    for meta in entries:
        try:
            import json
            data = json.loads(meta.read_text())
        except Exception:
            continue
        sid = data.get("session_id", meta.parent.name)
        status = data.get("status", "unknown")
        model = data.get("model", "?")
        created = data.get("created_at", "")
        click.echo(f"  {status:<9} {created:<28} {model:<16} {sid}")
    return 0


# ─── status ───────────────────────────────────────────────────
@cli.command()
@click.option("--project", "-p", default=".", show_default=True)
def status(project: str) -> int:
    """Show proxy health and current configuration."""
    cfg = _build_config(project)
    proxy = ProxyManager(cfg)
    healthy = proxy.health_check()
    click.echo(f"platform     : {cfg.platform.value}")
    click.echo(f"project      : {cfg.project_root}")
    click.echo(f"provider     : {cfg.provider}")
    click.echo(f"model        : {cfg.model}")
    click.echo(f"proxy mode   : {cfg.provider_mode}")
    click.echo(f"proxy health : {'ok' if healthy else 'down'}")
    return 0 if healthy else 1


# ─── stop proxy ───────────────────────────────────────────────
@cli.command()
@click.option("--project", "-p", default=".", show_default=True)
def stop(project: str) -> int:
    """Stop the provider proxy service."""
    cfg = _build_config(project)
    ProxyManager(cfg).stop()
    click.echo("Proxy stopped.")
    return 0


if __name__ == "__main__":
    cli()  # pragma: no cover
