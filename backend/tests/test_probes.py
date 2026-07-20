import hashlib

import httpx
import pytest

from app.models.monitoring import STATUS_DOWN, STATUS_UP
from app.probes import http as http_probe
from app.probes import tcp as tcp_probe
from app.probes import tls as tls_probe
from app.probes.base import run_probe


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_http_probe_success(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="hello world")

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(http_probe.httpx, "Client", fake_client)
    out = http_probe.execute("example.com", {"url": "http://example.com", "expected_status": 200}, 5)
    assert out.status == STATUS_UP
    assert out.latency_ms is not None


def test_http_probe_wrong_status(monkeypatch):
    def handler(request):
        return httpx.Response(500)

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(http_probe.httpx, "Client", fake_client)
    out = http_probe.execute("example.com", {"url": "http://example.com", "expected_status": 200}, 5)
    assert out.status == STATUS_DOWN
    assert "500" in out.error


def test_http_probe_body_substring(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="nothing here")

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(http_probe.httpx, "Client", fake_client)
    out = http_probe.execute(
        "example.com",
        {"url": "http://example.com", "expected_status": 200, "expected_body_substr": "welcome"},
        5,
    )
    assert out.status == STATUS_DOWN


def test_tcp_probe_missing_port():
    out = tcp_probe.execute("example.com", {}, 5)
    assert out.status == STATUS_DOWN
    assert "port" in out.error


def test_tcp_probe_refused():
    # Port 1 is essentially never open on localhost.
    out = tcp_probe.execute("127.0.0.1", {"port": 1}, 1)
    assert out.status == STATUS_DOWN


def test_run_probe_unknown_type():
    out = run_probe("ftp", "example.com", {}, 5)
    assert out.status == STATUS_DOWN
    assert "unknown probe type" in out.error


def _fake_getaddrinfo(addrs):
    # Mimic socket.getaddrinfo's 5-tuple shape; only the sockaddr's first element
    # (the IP string) matters to _resolve_ips.
    return lambda host, port: [(None, None, None, "", (a, 0)) for a in addrs]


def test_resolve_ips_sorts_and_dedupes(monkeypatch):
    monkeypatch.setattr(
        http_probe.socket, "getaddrinfo",
        _fake_getaddrinfo(["5.6.7.8", "1.2.3.4", "1.2.3.4"]),
    )
    assert http_probe._resolve_ips("example.com") == ["1.2.3.4", "5.6.7.8"]


def test_resolve_ips_returns_none_on_failure(monkeypatch):
    import socket as _socket

    def boom(host, port):
        raise _socket.gaierror("nope")

    monkeypatch.setattr(http_probe.socket, "getaddrinfo", boom)
    assert http_probe._resolve_ips("nx.invalid") is None
    assert http_probe._resolve_ips("") is None


def test_http_probe_track_ip_attaches_meta(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="ok")

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(http_probe.httpx, "Client", fake_client)
    monkeypatch.setattr(http_probe.socket, "getaddrinfo", _fake_getaddrinfo(["1.2.3.4"]))
    out = http_probe.execute(
        "example.com",
        {"url": "http://example.com", "expected_status": 200, "track_ip": True},
        5,
    )
    assert out.status == STATUS_UP
    assert out.meta == {"resolved_ips": ["1.2.3.4"]}


def test_tls_fingerprint_of_der():
    der = b"\x30\x82fake-der-bytes"
    assert tls_probe._fingerprint(der) == hashlib.sha256(der).hexdigest()
    assert tls_probe._fingerprint(b"") is None
    assert tls_probe._fingerprint(None) is None


def test_http_probe_no_track_ip_leaves_meta_empty(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="ok")

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = _mock_transport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(http_probe.httpx, "Client", fake_client)
    out = http_probe.execute("example.com", {"url": "http://example.com"}, 5)
    assert out.status == STATUS_UP
    assert out.meta is None
