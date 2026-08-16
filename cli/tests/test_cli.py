"""Unit tests for clanker.cli — entrypoint helpers and commands.

NOTE: These require `click` (and thus run after `uv sync` / a normal dev
environment with dependencies installed). The `cli.py` module imports
click at module load time.
"""
import sys
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

from clanker.cli import (
    _build_config,
    _collect_initial_prompt,
    _print_banner,
    _resolve_proxy,
    cli,
)
from clanker.config import Platform, Config
from clanker.session import Session


# ─── _build_config ────────────────────────────────────────────
def test_build_config_defaults(monkeypatch, tmp_path):
    # ensure platform resolves (linux/darwin)
    monkeypatch.chdir(tmp_path)
    cfg = _build_config(".", cache_dir=str(tmp_path / "c"),
                        secrets_dir=str(tmp_path / "s"))
    assert cfg.project_root == tmp_path.resolve()
    assert cfg.platform in (Platform.LINUX, Platform.MACOS)


def test_build_config_overrides_model_provider():
    cfg = _build_config(".", model="m", provider="p")
    assert cfg.model == "m"
    assert cfg.provider == "p"


# ─── _collect_initial_prompt ──────────────────────────────────
def test_prompt_takes_priority_over_editor(monkeypatch):
    monkeypatch.setattr(sys, "stdin", mock.Mock(read=lambda: "from-stdin"))
    with mock.patch("clanker.cli._open_editor_and_get_text", return_value="editor"):
        assert _collect_initial_prompt("arg-prompt", pipe=False, editor=True) == "arg-prompt"


def test_pipe_reads_stdin(monkeypatch):
    monkeypatch.setattr(sys, "stdin", mock.Mock(read=lambda: "stdin text"))
    assert _collect_initial_prompt(None, pipe=True, editor=False) == "stdin text"


def test_editor_opened_when_no_prompt_or_pipe():
    with mock.patch("clanker.cli._open_editor_and_get_text", return_value="ed text"):
        assert _collect_initial_prompt(None, pipe=False, editor=True) == "ed text"


def test_none_returned_when_no_source():
    assert _collect_initial_prompt(None, pipe=False, editor=False) is None


# ─── _open_editor_and_get_text ────────────────────────────────
def test_open_editor_returns_file_text(monkeypatch, tmp_path):
    payload = "draft prompt"
    fake_tmp = mock.Mock()
    fake_tmp.__enter__ = mock.Mock(return_value=mock.Mock(name=payload))
    fake_tmp.__exit__ = mock.Mock(return_value=False)
    # Build a tempfile.NamedTemporaryFile replacement that yields a real path
    import tempfile

    real_tf = tempfile.NamedTemporaryFile
    monkeypatch.setattr(
        tempfile, "NamedTemporaryFile",
        lambda *a, **kw: (Path(tmp_path / "draft.md").write_text(payload)
                          if (tmp_path / "draft.md") else None) or mock_tf(kw),
    )
    # Simpler: test via a stub subprocess that writes to the temp file.
    # Instead, create a real temp file via the actual mechanism.
    def fake_subprocess(args, check=False):
        Path(args[1]).write_text(payload)
        return mock.Mock()

    monkeypatch.setenv("EDITOR", "true")

    # monkeypatch tempfile to always point at a fixed file
    created = []
    def my_tf(**kw):
        p = Path(tmp_path / "editor.md")
        created.append(p)
        f = mock.Mock()
        f.name = str(p)
        return mock.Mock(__enter__=mock.Mock(return_value=f))
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", my_tf)
    monkeypatch.setattr("subprocess.run", fake_subprocess)

    from clanker.cli import _open_editor_and_get_text
    result = _open_editor_and_get_text()
    assert result == payload
    # temp file cleaned up
    assert not created[0].exists()


# ─── _print_banner ────────────────────────────────────────────
def test_print_banner_contains_key_rows(tmp_path, capsys):
    proxy = mock.Mock(health_check=lambda: True)
    cfg = Config(Platform.LINUX, tmp_path)
    s = Session.create(cfg.sessions_root, tmp_path)
    _print_banner(cfg, s, proxy, booted_proxy=True)
    out = capsys.readouterr().out
    assert "agent environment" in out
    assert str(cfg.project_root) in out
    assert cfg.provider in out
    assert cfg.model in out
    assert cfg.provider_mode in out
    assert s.session_id in out
    assert "booted" in out


def test_print_banner_shows_offline_proxy(tmp_path, capsys):
    cfg = Config(Platform.LINUX, tmp_path)
    s = Session.create(cfg.sessions_root, tmp_path)
    proxy = mock.Mock(health_check=lambda: False)
    _print_banner(cfg, s, proxy, booted_proxy=False)
    assert "offline" in capsys.readouterr().out


# ─── _resolve_proxy ───────────────────────────────────────────
def test_resolve_proxy_offline_mode_sets_fields(tmp_path):
    cfg = Config(Platform.LINUX, tmp_path)
    proxy, booted = _resolve_proxy(cfg, start=False)
    assert booted is False
    assert cfg.provider_mode == "offline"
    assert cfg.provider_endpoint == ""
    assert cfg.provider_socket_host is None


def test_resolve_proxy_returns_not_booted_when_healthy(tmp_path):
    cfg = Config(Platform.LINUX, tmp_path)
    with mock.patch.object(type(cfg), "provider_mode", "socket"):
        pass
    proxy, booted = _resolve_proxy(cfg, start=True)
    # health is not healthy by default; this would try to start.
    # To assert the happy path, monkeypatch ProxyManager.
    with mock.patch("clanker.cli.ProxyManager") as PM:
        inst = PM.return_value
        inst.health_check.return_value = True
        _p, _b = _resolve_proxy(cfg, start=True)
        assert _b is False
        PM.return_value.wait_for_health.assert_not_called()


def test_resolve_proxy_boots_when_down_then_healthy(tmp_path):
    cfg = Config(Platform.LINUX, tmp_path)
    with mock.patch("clanker.cli.ProxyManager") as PM:
        inst = PM.return_value
        inst.health_check.side_effect = [False, True]
        inst.wait_for_health.return_value = True
        _p, booted = _resolve_proxy(cfg, start=True)
        assert booted is True
        inst.start.assert_called_once()


def test_resolve_proxy_falls_back_offline_when_start_fails(tmp_path):
    cfg = Config(Platform.LINUX, tmp_path)
    with mock.patch("clanker.cli.ProxyManager") as PM:
        inst = PM.return_value
        inst.health_check.return_value = False
        inst.start.side_effect = RuntimeError("nope")
        _p, booted = _resolve_proxy(cfg, start=True)
        assert booted is False
        assert cfg.provider_mode == "offline"


def test_resolve_proxy_falls_back_offline_when_never_healthy(tmp_path):
    cfg = Config(Platform.LINUX, tmp_path)
    with mock.patch("clanker.cli.ProxyManager") as PM:
        inst = PM.return_value
        inst.health_check.side_effect = [False, False]
        inst.wait_for_health.return_value = False
        _p, booted = _resolve_proxy(cfg, start=True)
        assert booted is False
        assert cfg.provider_mode == "offline"


# ─── command surface (CliRunner) ──────────────────────────────
def test_cli_group_runs():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    for name in ("run", "prompt", "resume", "log", "sessions", "status", "stop"):
        assert name in result.output


def test_prompt_empty_arg_returns_error():
    runner = CliRunner()
    with mock.patch("clanker.cli._build_config") as bc:
        bc.side_effect = AssertionError("_build_config should not be reached")
        result = runner.invoke(cli, ["prompt"], input="")
        # No text anywhere -> exit code 2
        assert result.exit_code == 2
        assert "no text given" in result.output


def test_resume_missing_session_returns_error(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    #from pudb import set_trace; set_trace()
    runner = CliRunner()
    result = runner.invoke(cli, ["resume", "does-not-exist"])
    assert result.exit_code == 1
    assert "No session" in result.output
