"""Platform-aware configuration for the clanker CLI.

This module is the *single source of truth* for all paths and docker
knobs. Both ``DockerManager`` and ``ProxyManager`` read from a ``Config``
instance, and the CLI entrypoint builds one before doing any work.

The constructor is pure (no I/O). Any directory creation is deferred to
the callers, so configuration can be imported anywhere without side
effects.
"""
from __future__ import annotations

import os
import platform as _platform
from enum import Enum
from pathlib import Path


class Platform(Enum):
    """Host platform, driving provider transport & service management."""
    LINUX = "linux"
    MACOS = "darwin"



REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# The docker build context is the repository root (where the Dockerfile
# and the docker/ agent-loop live).
DOCKERFILE_DIR = REPO_ROOT

IMAGE_TAG = "clanker:latest"

DEFAULT_MODEL = os.environ.get("CLANKER_MODEL", "deepseek-chat")
DEFAULT_PROVIDER = os.environ.get("CLANKER_PROVIDER", "deepseek")


def get_platform() -> Platform:
    """Resolve the current host platform."""
    system = _platform.system().lower()
    if system == "darwin":
        return Platform.MACOS
    if system == "linux":
        return Platform.LINUX
    raise RuntimeError(f"Unsupported platform: {system}")


def find_repo_root() -> Path:
    """Locate the clanker repo root by walking up from this file.

    The root is recognized by the co-presence of a ``docker/`` directory
    (the image source) and a ``skills/`` directory (default skills).
    """
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "docker").is_dir() and (current / "skills").is_dir():
            return current
        current = current.parent
    return REPO_ROOT


class Config:
    """Runtime configuration for one clanker invocation."""

    def __init__(
        self,
        platform: Platform,
        project_root: Path,
        *,
        image_tag: str = IMAGE_TAG,
        model: str = DEFAULT_MODEL,
        provider: str = DEFAULT_PROVIDER,
        cache_dir: Path | None = None,
        secrets_dir: Path | None = None,
        skills_dir: Path | None = None,
    ) -> None:
        self.platform: Platform = platform
        self.project_root: Path = Path(project_root).expanduser().resolve()
        self.image_tag: str = image_tag
        self.dockerfile_dir: Path = DOCKERFILE_DIR
        self.model: str = os.environ.get("CLANKER_MODEL", None) or model
        self.provider: str = os.environ.get("CLANKER_PROVIDER", None) or provider

        self.provider_mode: str = "none"  # default here so that there is something to patch in test

        home = Path.home()
        self.cache_dir: Path = (
            Path(cache_dir).expanduser()
            if cache_dir is not None
            else Path(os.environ.get("CLANKER_CACHE", home / ".cache" / "clanker"))
        )
        self.secrets_dir: Path = (
            Path(secrets_dir).expanduser()
            if secrets_dir is not None
            else Path(
                os.environ.get(
                    "CLANKER_SECRETS_DIR", home / ".config" / "clanker" / "secrets"
                )
            )
        )
        self.skills_dir: Path = (
            Path(skills_dir).expanduser()
            if skills_dir is not None
            else find_repo_root() / "skills"
        )
        self.sessions_root: Path = self.cache_dir / "sessions"

        # Volume-mount option for /workspace (delegated on macOS for speed).
        self.workspace_mount_opts: str = "rw,delegated" if platform == Platform.MACOS else "rw"

        self._configure_provider(platform)

    # ── Provider / proxy ─────────────────────────────────────
    def _configure_provider(self, platform: Platform) -> None:
        if platform == Platform.MACOS:
            self.proxy_service: str = "com.clanker.provider-proxy"
            self.proxy_launch_agent_path: str = "~/Library/LaunchAgents/com.clanker.provider-proxy.plist"
            self.proxy_tcp_port: int = 11434
            # macOS reaches the host proxy over the docker bridge.
            self.provider_mode: str = "tcp"
            self.provider_endpoint: str = "http://host.docker.internal:11434"
            self.provider_socket_host: Path | None = None
            self.provider_socket_container: str = ""
        else:  # Linux
            self.proxy_service = "clanker-proxy.service"
            self.proxy_tcp_port = 11434
            # Linux uses a mounted unix socket, network is disabled.
            self.provider_mode = "socket"
            self.provider_endpoint = ""
            self.provider_socket_host = self.cache_dir / "provider.sock"
            self.provider_socket_container = "/var/run/provider.sock"

    # ── Derived helpers used by the entrypoint ───────────────
    def ensure_dirs(self) -> None:
        """Create cache / secret directory trees (used before docker run)."""
        (self.cache_dir / "pip").mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "npm").mkdir(parents=True, exist_ok=True)
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_root.mkdir(parents=True, exist_ok=True)

    @property
    def project_name(self) -> str:
        """Sanitized project basename, safe to use in paths and volumes."""
        name = self.project_root.name
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)

    def provider_key_file(self) -> Path:
        return self.secrets_dir / "provider.key"
