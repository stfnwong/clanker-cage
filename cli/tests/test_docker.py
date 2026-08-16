"""Unit tests for clanker.docker — the DockerManager build helpers.

These focus on the pure argument-construction helpers. Real docker
invocations are covered by integration tests (out of scope here).
"""
import os
from pathlib import Path
from unittest import mock

import pytest

from clanker.config import Config, Platform
from clanker.docker import DockerManager
from clanker.session import Session


def make_manager(tmp_path, platform=Platform.LINUX, **kw):
    skills = tmp_path / "skills"
    skills.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        platform,
        tmp_path / "proj",
        cache_dir=tmp_path / "cache",
        secrets_dir=tmp_path / "secrets",
        skills_dir=skills,
        **kw,
    )
    cfg.ensure_dirs()
    return DockerManager(cfg)


def make_session(manager):
    return Session.create(
        manager.config.sessions_root, manager.config.project_root
    )


# ─── mounts ───────────────────────────────────────────────────
def test_build_mount_args_includes_standard_mounts(tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)
    mounts, tmp = dm._build_mount_args(s)
    join = " | ".join(mounts)
    assert f"{dm.config.project_root}:/workspace:rw" in join
    assert f"{dm.config.cache_dir / 'pip'}:/home/clanker/.cache/pip:rw" in join
    assert f"{dm.config.cache_dir / 'npm'}:/home/clanker/.cache/npm:rw" in join
    assert f"{dm.config.skills_dir}:/etc/clanker/skills:ro" in join
    assert f"{s.session_dir}:/session:rw" in join


def test_build_mount_args_linux_socket_mount(tmp_path):
    dm = make_manager(tmp_path, platform=Platform.LINUX)
    s = make_session(dm)
    mounts, _ = dm._build_mount_args(s)
    assert f"{dm.config.provider_socket_host}:/var/run/provider.sock:rw" in " | ".join(mounts)


def test_build_mount_args_tcp_no_socket(tmp_path):
    dm = make_manager(tmp_path, platform=Platform.MACOS)
    s = make_session(dm)
    mounts, _ = dm._build_mount_args(s)
    assert not any("provider.sock" in m for m in mounts)


def test_project_skills_mounted_when_present(tmp_path):
    dm = make_manager(tmp_path)
    proj_skills = dm.config.project_root / ".clanker" / "skills"
    proj_skills.mkdir(parents=True)
    (proj_skills / "x.md").write_text("y")
    s = make_session(dm)
    mounts, _ = dm._build_mount_args(s)
    expected = f"{proj_skills}:/workspace/.clanker/skills:ro"
    assert expected in mounts


def test_project_skills_not_mounted_when_absent(tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)
    mounts, _ = dm._build_mount_args(s)
    assert not any(".clanker/skills" in m for m in mounts)


def test_project_config_mounted_when_present(tmp_path):
    dm = make_manager(tmp_path)
    proj_cfg = dm.config.project_root / ".clanker" / "config"
    proj_cfg.parent.mkdir(parents=True)
    proj_cfg.write_text("{}")
    s = make_session(dm)
    mounts, _ = dm._build_mount_args(s)
    assert f"{proj_cfg}:/workspace/.clanker/config:ro" in mounts


def test_venv_shadowed_by_named_volume(tmp_path):
    dm = make_manager(tmp_path)
    venv = dm.config.project_root / ".venv"
    venv.mkdir(parents=True)
    s = make_session(dm)
    mounts, _ = dm._build_mount_args(s)
    assert f"clanker-venv-{dm.config.project_root.name}:/workspace/.venv" in mounts


def test_secret_key_mounted_when_key_exists(tmp_path):
    dm = make_manager(tmp_path)
    key = dm.config.provider_key_file()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"secret-key-content")
    s = make_session(dm)
    mounts, tmp_key = dm._build_mount_args(s)
    assert tmp_key is not None
    assert f"{tmp_key}:/run/secrets/provider.key:ro" in " | ".join(mounts)


def test_no_secret_mount_when_no_key(tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)
    mounts, tmp_key = dm._build_mount_args(s)
    assert tmp_key is None
    assert not any("/run/secrets/" in m for m in mounts)


def test_tmp_key_contains_key_contents(tmp_path):
    dm = make_manager(tmp_path)
    key = dm.config.provider_key_file()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"abc123")
    s = make_session(dm)
    _, tmp_key = dm._build_mount_args(s)
    try:
        assert Path(tmp_key).read_bytes() == b"abc123"
    finally:
        Path(tmp_key).unlink(missing_ok=True)


# ─── network args ─────────────────────────────────────────────
def test_network_socket_mode_disables_network(tmp_path):
    dm = make_manager(tmp_path, platform=Platform.LINUX)
    assert dm._build_network_args() == ["--network", "none"]


def test_network_tcp_mode_uses_bridge(tmp_path):
    dm = make_manager(tmp_path, platform=Platform.MACOS)
    assert dm._build_network_args() == []


# ─── environment ──────────────────────────────────────────────
def test_environment_contains_core_vars(tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)
    env = dm._build_environment(s, None)
    joined = " ".join(env)
    assert f"CLANKER_PROVIDER={dm.config.provider}" in joined
    assert f"CLANKER_PROVIDER_MODE={dm.config.provider_mode}" in joined
    assert f"CLANKER_MODEL={dm.config.model}" in joined
    assert f"CLANKER_SESSION_ID={s.session_id}" in joined
    assert f"CLANKER_PROJECT_NAME={dm.config.project_name}" in joined


def test_environment_includes_initial_prompt(tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)
    env = dm._build_environment(s, "do the thing")
    assert any("CLANKER_INITIAL_PROMPT=do the thing" in e for e in env)


def test_environment_omits_prompt_when_none(tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)
    env = dm._build_environment(s, None)
    assert not any("CLANKER_INITIAL_PROMPT" in e for e in env)


# ─── image existence ──────────────────────────────────────────
def test_image_exists_true_on_zero_rc(monkeypatch, tmp_path):
    dm = make_manager(tmp_path)

    def fake_run(cmd, **kw):
        assert cmd[:2] == ["docker", "image"]
        return mock.Mock(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert dm._image_exists("tag") is True


def test_image_exists_false_on_nonzero_rc(monkeypatch, tmp_path):
    dm = make_manager(tmp_path)
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: mock.Mock(returncode=1))
    assert dm._image_exists("tag") is False


def test_image_exists_raises_without_docker(monkeypatch, tmp_path):
    dm = make_manager(tmp_path)
    monkeypatch.setattr("subprocess.run", mock.Mock(side_effect=FileNotFoundError))
    with pytest.raises(RuntimeError, match="Docker command not found"):
        dm._image_exists("tag")


# ─── run pointer file cleanup ─────────────────────────────────
def test_run_cleans_up_temp_key_and_pointer_on_exit(monkeypatch, tmp_path):
    dm = make_manager(tmp_path)
    s = make_session(dm)

    # make an image exist so build is skipped
    monkeypatch.setattr(dm, "_image_exists", lambda tag: True)

    key = dm.config.provider_key_file()
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(b"k")
    s.mkdir()

    runner = mock.Mock(returncode=0)
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: runner)

    pointer = dm.config.cache_dir / "current-container"
    code = dm.run(s, initial_prompt=None)
    assert code == 0
    # pointer file removed after run
    assert not pointer.exists()
