from types import SimpleNamespace

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UNKNOWN, STATUS_UP
from app.probe_runner import _apply_latency_threshold, _display_status, _is_bad
from app.probes import ProbeOutcome


def _probe(latency_degraded_ms=None):
    return SimpleNamespace(latency_degraded_ms=latency_degraded_ms)


def _tier_probe(degraded_threshold=1, down_threshold=1):
    return SimpleNamespace(
        degraded_threshold=degraded_threshold, down_threshold=down_threshold
    )


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


def test_display_status_default_thresholds_down_immediately():
    # Defaults (1, 1): a hard-down check is "down" from the first failure.
    p = _tier_probe()
    assert _display_status(p, STATUS_DOWN, 1) == STATUS_DOWN
    assert _display_status(p, STATUS_DOWN, 5) == STATUS_DOWN


def test_display_status_three_tiers():
    # degraded after 1 failure, down after 3.
    p = _tier_probe(degraded_threshold=1, down_threshold=3)
    assert _display_status(p, STATUS_DOWN, 1) == STATUS_DEGRADED
    assert _display_status(p, STATUS_DOWN, 2) == STATUS_DEGRADED
    assert _display_status(p, STATUS_DOWN, 3) == STATUS_DOWN
    assert _display_status(p, STATUS_DOWN, 9) == STATUS_DOWN


def test_display_status_tolerates_early_failures():
    # degraded only after 2 failures: the first failure still shows up.
    p = _tier_probe(degraded_threshold=2, down_threshold=4)
    assert _display_status(p, STATUS_DOWN, 1) == STATUS_UP
    assert _display_status(p, STATUS_DOWN, 2) == STATUS_DEGRADED


def test_display_status_passes_through_non_down():
    # up / latency-degraded / unknown are never tiered.
    p = _tier_probe(degraded_threshold=1, down_threshold=3)
    assert _display_status(p, STATUS_UP, 0) == STATUS_UP
    assert _display_status(p, STATUS_DEGRADED, 5) == STATUS_DEGRADED
    assert _display_status(p, STATUS_UNKNOWN, 5) == STATUS_UNKNOWN
