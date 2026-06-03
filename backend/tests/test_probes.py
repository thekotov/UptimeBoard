import httpx
import pytest

from app.models.monitoring import STATUS_DOWN, STATUS_UP
from app.probes import http as http_probe
from app.probes import tcp as tcp_probe
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
