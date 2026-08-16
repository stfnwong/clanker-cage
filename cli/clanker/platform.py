import subprocess
import urllib
from pathlib import Path

from clanker.config import Config, Platform


class ProxyManager:
    def __init__(self, config: Config):
        self.config: Config = config

    def health_check(self):
        if self.config.platform == Platform.MACOS:
            return self._check_tcp_health()
        else:
            return self._check_socket_exists()

    def start(self):
        if self.config.platform == Platform.MACOS:
            subprocess.run(["launchctl", "bootstrap", ...])
            subprocess.run(["launchctl", "kickstart", ...])
        else:
            subprocess.run(["systemctl", "--user", "start", self.config.proxy_service])

    def stop(self):
        if self.config.platform == Platform.MACOS:
            subprocess.run(["launchctl", "bootout", ...])
        else:
            subprocess.run(["systemctl", "--user", "stop", self.config.proxy_service])

    def _check_tcp_health(self) -> bool:
        """
        Check that the TCP proxy is up by hitting its /health endpoint.
        Returns True only if we get a 200 and the body is 'ok'.
        """
        url = f"http://127.0.0.1:{self.config.proxy_tcp_port}/health"
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                return resp.status == 200 and resp.read().strip() == b"ok"
        except Exception:
            return False

    def _check_socket_exists(self) -> bool:
        """
        Check that the Unix socket exists and is actually a socket.
        """
        socket_path = Path(self.config.proxy_socket_host).expanduser()
        return socket_path.exists() and socket_path.is_socket()
