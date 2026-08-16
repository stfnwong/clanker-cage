"""Provider proxy lifecycle management.

The provider proxy is the host-side process that holds the API key and
exposes a single endpoint to the container. Depending on the platform it
is reached over a Unix socket (Linux) or TCP (macOS), and is managed as a
launchd job / systemd user service respectively.

This module is deliberately thin: it only decides *how* to talk to the
service. The CLI entrypoint owns *when* to start/stop it based on flags
and health checks.
"""
from __future__ import annotations

import os
import subprocess
import time
import urllib.request
from pathlib import Path

from clanker.config import Config, Platform


class ProxyManager:
    """Start/stop/health-check the provider proxy for a platform."""

    def __init__(self, config: Config) -> None:
        self.config = config

    # ── Health ───────────────────────────────────────────────
    def health_check(self) -> bool:
        """Return True if the proxy is reachable and healthy."""
        if self.config.platform == Platform.MACOS:
            return self.check_tcp()
        return self.check_socket()

    def wait_for_health(self, timeout: float = 5.0) -> bool:
        """Poll health_check() every 0.5s until healthy or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.health_check():
                return True
            time.sleep(0.5)
        return self.health_check()

    # ── Lifecycle ────────────────────────────────────────────
    def start(self) -> None:
        """Start the proxy if it isn't already running."""
        if self.config.platform == Platform.MACOS:
            label = self.config.proxy_service
            plist = Path(self.config.proxy_launch_agent_path).expanduser()
            # bootstrap if not loaded, then kickstart to force (re)start
            subprocess.run(
                ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                capture_output=True,
            )
            subprocess.run(
                ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{label}"],
                check=True,
            )
        else:
            subprocess.run(
                ["systemctl", "--user", "start", self.config.proxy_service],
                check=True,
            )

    def stop(self) -> None:
        """Stop the proxy. Never raises on failure (best-effort)."""
        if self.config.platform == Platform.MACOS:
            subprocess.run(
                ["launchctl", "bootout", f"gui/{os.getuid()}/{self.config.proxy_service}"],
                capture_output=True,
            )
        else:
            subprocess.run(
                ["systemctl", "--user", "stop", self.config.proxy_service],
                capture_output=True,
            )

    # ── Internals ────────────────────────────────────────────
    def check_tcp(self) -> bool:
        """Health check for the TCP mode proxy (/health must return 'ok')."""
        url = f"http://127.0.0.1:{self.config.proxy_tcp_port}/health"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200 and resp.read().strip() == b"ok"
        except Exception:
            return False

    def check_socket(self) -> bool:
        """Health check for the unix-socket mode proxy."""
        socket_path = self.config.provider_socket_host
        return bool(socket_path) and socket_path.exists() and socket_path.is_socket()

