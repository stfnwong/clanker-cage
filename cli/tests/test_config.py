"""Unit tests for clanker.config — platform-aware configuration."""
import os
from pathlib import Path

import pytest

from clanker.config import Config, Platform, DEFAULT_MODEL, DEFAULT_PROVIDER


# ─── fixture helpers ──────────────────────────────────────────
def make_config(platform, project, **kw):
    return Config(platform, Path(project), **kw)


# ─── constructor / path resolution ────────────────────────────
def test_constructor_resolves_and_expands_project(tmp_path):
    cfg = make_config(Platform.LINUX, tmp_path / "proj")
    assert cfg.project_root == (tmp_path / "proj").resolve()


def test_explicit_cache_and_secrets_dirs_are_expanded(tmp_path):
    cfg = make_config(
        Platform.LINUX,
        tmp_path / "proj",
        cache_dir=tmp_path / "cache",
        secrets_dir=tmp_path / "secrets",
    )
    assert cfg.cache_dir == (tmp_path / "cache").resolve()
    assert cfg.secrets_dir == (tmp_path / "secrets").resolve()


def test_sessions_root_derived_from_cache_dir(tmp_path):
    cfg = make_config(Platform.LINUX, tmp_path / "proj", cache_dir=tmp_path / "cache")
    assert cfg.sessions_root == (tmp_path / "cache" / "sessions").resolve()


def test_default_model_and_provider():
    cfg = make_config(Platform.LINUX, "/proj")
    assert cfg.model == DEFAULT_MODEL
    assert cfg.provider == DEFAULT_PROVIDER


def test_can_override_model_and_provider():
    cfg = make_config(Platform.LINUX, "/proj", model="gpt-4o", provider="openai")
    assert cfg.model == "gpt-4o"
    assert cfg.provider == "openai"


def test_env_vars_used_as_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("CLANKER_MODEL", "env-model")
    monkeypatch.setenv("CLANKER_PROVIDER", "env-provider")
    cfg = make_config(Platform.LINUX, "/proj")
    assert cfg.model == "env-model"
    assert cfg.provider == "env-provider"


# ─── platform-specific provider config ────────────────────────
def test_linux_uses_socket_mode():
    cfg = make_config(Platform.LINUX, "/proj", cache_dir=Path("/cache"))
    assert cfg.provider_mode == "socket"
    assert cfg.provider_endpoint == ""
    assert cfg.provider_socket_container == "/var/run/provider.sock"
    # socket host derived from cache dir
    assert cfg.provider_socket_host == Path("/cache") / "provider.sock"
    assert cfg.workspace_mount_opts == "rw"


def test_macos_uses_tcp_mode_and_delegated_mount():
    cfg = make_config(Platform.MACOS, "/proj")
    assert cfg.provider_mode == "tcp"
    assert cfg.provider_endpoint == "http://host.docker.internal:11434"
    assert cfg.provider_socket_host is None
    assert cfg.workspace_mount_opts == "rw,delegated"


# ─── project_name sanitization ────────────────────────────────
@pytest.mark.parametrize(
    "name,expected",
    [
        ("simple", "simple"),
        ("My.Project dir", "My_Project_dir"),
        ("has/slash\\back", "has_slash_back"),
        ("with-mixed_2", "with-mixed_2"),
    ],
)
def test_project_name_sanitizes(tmp_path, name, expected):
    cfg = make_config(Platform.LINUX, tmp_path / name)
    assert cfg.project_name == expected


# ─── ensure_dirs ──────────────────────────────────────────────
def test_ensure_dirs_creates_layout(tmp_path):
    cfg = make_config(
        Platform.LINUX,
        tmp_path / "proj",
        cache_dir=tmp_path / "cache",
        secrets_dir=tmp_path / "secrets",
    )
    cfg.ensure_dirs()
    assert (tmp_path / "cache" / "pip").is_dir()
    assert (tmp_path / "cache" / "npm").is_dir()
    assert (tmp_path / "secrets").is_dir()
    assert (tmp_path / "cache" / "sessions").is_dir()


def test_ensure_dirs_is_idempotent(tmp_path):
    cfg = make_config(
        Platform.LINUX,
        tmp_path / "proj",
        cache_dir=tmp_path / "cache",
        secrets_dir=tmp_path / "secrets",
    )
    cfg.ensure_dirs()
    cfg.ensure_dirs()  # must not raise
    assert (tmp_path / "cache" / "pip").is_dir()


# ─── provider_key_file ────────────────────────────────────────
def test_provider_key_file_is_under_secrets(tmp_path):
    cfg = make_config(
        Platform.LINUX, tmp_path / "proj", secrets_dir=tmp_path / "secrets"
    )
    assert cfg.provider_key_file() == (tmp_path / "secrets" / "provider.key").resolve()
