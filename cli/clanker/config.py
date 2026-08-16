from enum import Enum
from pathlib import Path

from cli import platform


class Platform(Enum):
    LINUX = "linux"
    MACOS = "darwin"


def get_platform():
    system = platform.system().lower()
    if system == "darwin":
        return Platform.MACOS
    elif system == "linux":
        return Platform.LINUX
    else:
        raise RuntimeError(f"Unsupported platform: {system}")



class Config:
    def __init__(self, platform: Platform):
        self.platform: Platform = platform

        if platform == Platform.MACOS:
            self.proxy_service: str = "com.clanker.provider-proxy"
            self.proxy_launch_agent_path: str = "~/Library/LaunchAgents/com.clanker.provider-proxy.plist"
            self.proxy_mode: str = "tcp"
            self.proxy_endpoint: str = "http://host.docker.internal:11434"
            self.network: str = ""
            self.socket_mount: str = ""
        else:  # Linux
            self.proxy_service = "clanker-proxy.service"
            self.proxy_mode = "socket"
            self.proxy_socket_host: str = "~/.cache/clanker/provider.sock"
            self.proxy_socket_container: str = "/var/run/provider.sock"
            self.network: str = "--network none"
            self.socket_mount: str = f"-v {self.proxy_socket_host}:{self.proxy_socket_container}:rw"



def find_repo_root():
    current = Path(__file__).resolve().parent
    for _ in range(5):
        if (current / "docker").is_dir() and (current / "skills").is_dir():
            return current
        current = current.parent
    raise RuntimeError("clanker repo root not found")
