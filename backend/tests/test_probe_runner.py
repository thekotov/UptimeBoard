from datetime import datetime, timezone
from types import SimpleNamespace

from app import probe_runner as pr
from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UNKNOWN, STATUS_UP
from app.probe_runner import _apply_latency_threshold, _display_status, _is_bad
from app.probes import ProbeOutcome


def _probe(latency_degraded_ms=None):
    return SimpleNamespace(latency_degraded_ms=latency_degraded_ms)


def _tier_probe(degraded_threshold=1, down_threshold=1, tolerance_checks=0):
    return SimpleNamespace(
        degraded_threshold=degraded_threshold,
        down_threshold=down_threshold,
        tolerance_checks=tolerance_checks,
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


# ---- IP-change tracking (handle_ip_change) ----

_NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)


def _ip_probe(last_ip):
    server = SimpleNamespace(name="srv", service=SimpleNamespace(name="svc", page=None))
    return SimpleNamespace(id=1, name="p", type="http", server_id=2, server=server, last_ip=last_ip)


def _capture_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(pr, "base_channels", lambda db, page_id: ["chan"])
    monkeypatch.setattr(pr, "dispatch", lambda channels, **kw: calls.append(kw))
    return calls


def test_ip_change_alerts_on_change(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["2.2.2.2"]})
    pr.handle_ip_change(None, probe, outcome, "example.com", 3, False, _NOW)
    assert probe.last_ip == "2.2.2.2"
    assert len(calls) == 1
    assert calls[0]["event"] == "ip_changed"
    assert calls[0]["group"] is False
    assert "1.1.1.1" in calls[0]["error"] and "2.2.2.2" in calls[0]["error"]


def test_ip_change_first_observation_is_silent(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe(None)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["1.1.1.1"]})
    pr.handle_ip_change(None, probe, outcome, "example.com", 3, False, _NOW)
    assert probe.last_ip == "1.1.1.1"
    assert calls == []


def test_ip_change_same_set_no_alert(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1, 2.2.2.2")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["1.1.1.1", "2.2.2.2"]})
    pr.handle_ip_change(None, probe, outcome, "example.com", 3, False, _NOW)
    assert calls == []


def test_ip_change_suppressed_in_maintenance(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"resolved_ips": ["2.2.2.2"]})
    pr.handle_ip_change(None, probe, outcome, "example.com", 3, True, _NOW)
    # still records the new set, but sends nothing during maintenance
    assert probe.last_ip == "2.2.2.2"
    assert calls == []


def test_ip_change_no_meta_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _ip_probe("1.1.1.1")
    pr.handle_ip_change(None, probe, ProbeOutcome(status=STATUS_UP), "example.com", 3, False, _NOW)
    assert probe.last_ip == "1.1.1.1"
    assert calls == []


# ---- SSL-change tracking (handle_cert_change) ----


def _cert_probe(track, issuer="Let's Encrypt", subject="example.com", expires=None):
    server = SimpleNamespace(name="srv", service=SimpleNamespace(name="svc", page=None))
    # tls_* fields hold the just-applied (new) metadata; handle_cert_change gets the
    # previous snapshot separately.
    return SimpleNamespace(
        id=1, name="p", type="tls", server_id=2, server=server,
        config={"track_cert_change": track},
        tls_issuer=issuer, tls_subject=subject, tls_expires_at=expires,
    )


def test_cert_change_alerts_on_new_fingerprint(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True, issuer="ZeroSSL")
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "new"})
    prev = ("old", "Let's Encrypt", "example.com")  # (fp, issuer, subject)
    pr.handle_cert_change(None, probe, outcome, "example.com", 3, False, _NOW, prev)
    assert len(calls) == 1
    assert calls[0]["event"] == "cert_changed" and calls[0]["group"] is False
    # issuer moved -> shown as old → new
    assert "Let's Encrypt" in calls[0]["error"] and "ZeroSSL" in calls[0]["error"]


def test_cert_change_first_observation_is_silent(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "first"})
    pr.handle_cert_change(None, probe, outcome, "h", 3, False, _NOW, (None, None, None))
    assert calls == []


def test_cert_change_same_fingerprint_no_alert(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "same"})
    pr.handle_cert_change(None, probe, outcome, "h", 3, False, _NOW, ("same", "i", "s"))
    assert calls == []


def test_cert_change_disabled_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(False)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "new"})
    pr.handle_cert_change(None, probe, outcome, "h", 3, False, _NOW, ("old", "i", "s"))
    assert calls == []


def test_cert_change_suppressed_in_maintenance(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    outcome = ProbeOutcome(status=STATUS_UP, meta={"fingerprint": "new"})
    pr.handle_cert_change(None, probe, outcome, "h", 3, True, _NOW, ("old", "i", "s"))
    assert calls == []


def test_cert_change_no_fingerprint_is_noop(monkeypatch):
    calls = _capture_dispatch(monkeypatch)
    probe = _cert_probe(True)
    pr.handle_cert_change(None, probe, ProbeOutcome(status=STATUS_UP), "h", 3, False, _NOW, ("old", "i", "s"))
    assert calls == []
