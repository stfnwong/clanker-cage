"""Unit tests for clanker.platform — ProxyManager lifecycle + health."""
from pathlib import Path
from unittest import mock

from clanker.config import Config, Platform
from clanker.platform import ProxyManager


def make_proxy(tmp_path, platform):
    cfg = Config(
        platform,
        tmp_path / "proj",
        cache_dir=tmp_path / "cache",
        secrets_dir=tmp_path / "secrets",
    )
    return ProxyManager(cfg)


# ─── socket health (Linux) ────────────────────────────────────
def test_check_socket_true_when_socket_exists(tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    sock = proxy.config.provider_socket_host
    import socket
    sock.parent.mkdir(parents=True, exist_ok=True)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock))
    try:
        assert proxy.check_socket() is True
    finally:
        srv.close()
        sock.unlink(missing_ok=True)


def test_check_socket_false_when_missing(tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    assert proxy.check_socket() is False


# ─── tcp health (macOS) ───────────────────────────────────────
def test_check_tcp_true_on_ok(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.MACOS)

    class FakeResp:
        status = 200
        def read(self):
            return b"ok"

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url, timeout=2: FakeResp()
    )
    assert proxy.check_tcp() is True


def test_check_tcp_false_on_non_ok_body(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.MACOS)

    class FakeResp:
        status = 200
        def read(self):
            return b"not ok"

    monkeypatch.setattr(
        "urllib.request.urlopen", lambda url, timeout=2: FakeResp()
    )
    assert proxy.check_tcp() is False


def test_check_tcp_false_on_error(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.MACOS)
    monkeypatch.setattr(
        "urllib.request.urlopen",
        mock.Mock(side_effect=OSError("refused")),
    )
    assert proxy.check_tcp() is False


# ─── health_check dispatch ────────────────────────────────────
def test_health_check_dispatches_to_tcp_for_macos(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.MACOS)
    monkeypatch.setattr(proxy, "check_tcp", lambda: "tcp-result")
    assert proxy.health_check() == "tcp-result"


def test_health_check_dispatches_to_socket_for_linux(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    monkeypatch.setattr(proxy, "check_socket", lambda: "socket-result")
    assert proxy.health_check() == "socket-result"


# ─── wait_for_health ──────────────────────────────────────────
def test_wait_for_health_immediate_true(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    monkeypatch.setattr(proxy, "health_check", lambda: True)
    assert proxy.wait_for_health(timeout=0.5) is True


def test_wait_for_health_eventually_true(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    calls = {"n": 0}
    def flaky():
        calls["n"] += 1
        return calls["n"] >= 3
    monkeypatch.setattr(proxy, "health_check", flaky)
    monkeypatch.setattr("time.sleep", lambda s: None)  # don't actually sleep
    assert proxy.wait_for_health(timeout=1.0) is True
    assert calls["n"] <= 4


def test_wait_for_health_false_when_never_healthy(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    monkeypatch.setattr(proxy, "health_check", lambda: False)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert proxy.wait_for_health(timeout=0.1) is False


# ─── start / stop dispatch ────────────────────────────────────
def test_start_linux_uses_systemctl(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda args, **kw: (calls.append(args) or mock.Mock(returncode=0)),
    )
    proxy.start()
    assert calls[0][0] == ["systemctl", "--user", "start", "clanker-proxy.service"]


def test_start_macos_uses_launchctl(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.MACOS)
    calls = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda args, **kw: (calls.append(args) or mock.Mock(returncode=0)),
    )
    proxy.start()
    # bootstrap then kickstart
    assert "launchctl" in calls[0][0]
    assert "kickstart" in calls[1][0]


def test_stop_never_raises(monkeypatch, tmp_path):
    proxy = make_proxy(tmp_path, Platform.LINUX)
    monkeypatch.setattr("subprocess.run", mock.Mock(side_effect=OSError("boom")))
    proxy.stop()  # must not raise
