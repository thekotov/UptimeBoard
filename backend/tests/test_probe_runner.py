from types import SimpleNamespace

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UNKNOWN, STATUS_UP
from app.probe_runner import _apply_latency_threshold, _is_bad
from app.probes import ProbeOutcome


def _probe(latency_degraded_ms=None):
    return SimpleNamespace(latency_degraded_ms=latency_degraded_ms)


def test_is_bad():
    assert _is_bad(STATUS_DOWN) is True
    assert _is_bad(STATUS_DEGRADED) is True
    assert _is_bad(STATUS_UP) is False
    assert _is_bad(STATUS_UNKNOWN) is False


def test_latency_threshold_downgrades_when_slow():
    out = _apply_latency_threshold(_probe(100), ProbeOutcome(status=STATUS_UP, latency_ms=250))
    assert out.status == STATUS_DEGRADED
    assert "250" in out.error and "100" in out.error


def test_latency_threshold_keeps_fast_up():
    out = _apply_latency_threshold(_probe(100), ProbeOutcome(status=STATUS_UP, latency_ms=50))
    assert out.status == STATUS_UP


def test_latency_threshold_ignored_without_limit():
    out = _apply_latency_threshold(_probe(None), ProbeOutcome(status=STATUS_UP, latency_ms=9999))
    assert out.status == STATUS_UP


def test_latency_threshold_does_not_touch_down():
    out = _apply_latency_threshold(_probe(100), ProbeOutcome(status=STATUS_DOWN, latency_ms=None))
    assert out.status == STATUS_DOWN
