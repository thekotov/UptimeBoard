import hashlib
import re
import socket
import time
from urllib.parse import urlsplit

import httpx

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome, is_timeout_error


def _resolve_json_path(data, path: str):
    """Minimal dotted JSON path: a.b.c with optional [index], e.g. data.items[0].id."""
    cur = data
    for raw in path.split("."):
        if not raw:
            continue
        # split key and any [index] suffixes
        m = re.match(r"^([^\[\]]*)(.*)$", raw)
        key, idx_part = m.group(1), m.group(2)
        if key:
            if not isinstance(cur, dict) or key not in cur:
                raise KeyError(key)
            cur = cur[key]
        for idx in re.findall(r"\[(\d+)\]", idx_part):
            i = int(idx)
            if not isinstance(cur, list) or i >= len(cur):
                raise KeyError(idx)
            cur = cur[i]
    return cur


def execute(host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """HTTP probe.

    config keys:
      url, method (GET), expected_status (200)
      body                 request body sent with POST/PUT/PATCH
      content_type         Content-Type for the request body (default JSON)
      headers              dict of extra request headers
      basic_user/basic_pass   HTTP Basic auth
      bearer_token         Authorization: Bearer <token>
      expected_body_substr substring that must appear in the body
      expected_body_regex  regex that must match the body
      json_path/json_expected  resolve a JSON path and compare to expected (string)
      follow_redirects     bool (default True)
      check_cert           also inspect the TLS certificate (https URLs only)
      warn_days            cert "degraded" threshold in days (default 14)
      track_ip             resolve the host's IPs and report them in meta so the
                           runner can alert when they change
      track_content        hash the response body and report it in meta so the
                           runner can alert when the page content changes
    """
    url = config.get("url") or f"http://{host}"
    http_outcome = _http_check(url, config, timeout_sec)

    # Optional certificate tracking: when enabled on an https endpoint, also judge
    # the cert (expiry/trust) and fold it into the result. The probe status becomes
    # the worse of the HTTP and certificate verdicts, and cert metadata is attached.
    if config.get("check_cert") and urlsplit(url).scheme == "https":
        outcome = _merge(http_outcome, _cert_for_url(url, config, timeout_sec))
    else:
        outcome = http_outcome

    # Optional IP tracking: resolve the URL host and attach the current address set
    # to meta. The runner compares it against the last observed set and alerts on a
    # change. Skipped silently on resolution failure (no set -> no false alert).
    if config.get("track_ip"):
        ips = _resolve_ips(urlsplit(url).hostname or host)
        if ips:
            outcome.meta = {**(outcome.meta or {}), "resolved_ips": ips}
    return outcome


def _http_check(url: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    method = (config.get("method") or "GET").upper()
    expected_status = int(config.get("expected_status", 200))
    follow_redirects = bool(config.get("follow_redirects", True))

    headers = dict(config.get("headers") or {})
    if config.get("bearer_token"):
        headers["Authorization"] = f"Bearer {config['bearer_token']}"
    auth = None
    if config.get("basic_user"):
        auth = (config["basic_user"], config.get("basic_pass", ""))

    # Optional request body for write methods. Default the Content-Type to JSON
    # when a body is sent and none was set explicitly (via config or headers).
    content = None
    if method in ("POST", "PUT", "PATCH") and config.get("body"):
        content = str(config["body"]).encode("utf-8")
        ct = config.get("content_type")
        if ct:
            headers["Content-Type"] = ct
        elif not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json"

    start = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_sec, follow_redirects=follow_redirects) as client:
            resp = client.request(method, url, headers=headers or None, auth=auth, content=content)
    except httpx.HTTPError as exc:
        # Surface request failures here (instead of letting the runner wrap them)
        # so certificate tracking can still run and classify a TLS error nicely.
        return ProbeOutcome(
            status=STATUS_DOWN, error=str(exc),
            kind="timeout" if is_timeout_error(exc) else None,
        )
    latency_ms = (time.perf_counter() - start) * 1000

    # Content tracking: hash the raw response body so the runner can detect when
    # the served page changes. Attached to every outcome below (even failures).
    meta = None
    if config.get("track_content"):
        meta = {"content_hash": hashlib.sha256(resp.content).hexdigest()}

    def down(msg: str) -> ProbeOutcome:
        return ProbeOutcome(status=STATUS_DOWN, latency_ms=latency_ms, error=msg, meta=meta)

    if resp.status_code != expected_status:
        return down(f"expected status {expected_status}, got {resp.status_code}")
    if config.get("expected_body_substr") and config["expected_body_substr"] not in resp.text:
        return down("expected body substring not found")
    if config.get("expected_body_regex"):
        try:
            if not re.search(config["expected_body_regex"], resp.text):
                return down("body regex did not match")
        except re.error as exc:
            return down(f"invalid regex: {exc}")
    if config.get("json_path"):
        try:
            value = _resolve_json_path(resp.json(), config["json_path"])
        except (KeyError, ValueError) as exc:
            return down(f"json path not found: {exc}")
        expected = config.get("json_expected")
        if expected is not None and str(value) != str(expected):
            return down(f"json path = {value!r}, expected {expected!r}")

    return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms, meta=meta)


def _resolve_ips(host: str) -> list[str] | None:
    """Resolve ``host`` to the sorted, de-duplicated set of its IP addresses
    (both IPv4 and IPv6). Returns None if the host is empty, is unresolvable, or
    is already a bare IP with no addresses to report."""
    if not host:
        return None
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return None
    ips = sorted({info[4][0] for info in infos})
    return ips or None


def _cert_for_url(url: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """Evaluate the TLS certificate of an https URL's host."""
    # Imported lazily to avoid a circular import at module load.
    from app.probes.tls import check_certificate

    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 443
    warn_days = int(config.get("warn_days", 14))
    return check_certificate(host, port, timeout_sec, warn_days)


# Status severity for folding the HTTP and certificate verdicts together.
_RANK = {STATUS_UP: 0, STATUS_DEGRADED: 1, STATUS_DOWN: 2}


def _merge(http_outcome: ProbeOutcome, cert_outcome: ProbeOutcome) -> ProbeOutcome:
    """Combine the HTTP and certificate verdicts into one outcome: status is the
    worse of the two, and a bad certificate wins ties (its message is the most
    actionable). Metadata from both sides is carried through (HTTP content hash +
    certificate fields)."""
    cert_bad = cert_outcome.status != STATUS_UP
    if cert_bad and _RANK[cert_outcome.status] >= _RANK[http_outcome.status]:
        chosen = cert_outcome
    else:
        chosen = http_outcome
    meta = {**(http_outcome.meta or {}), **(cert_outcome.meta or {})}
    return ProbeOutcome(
        status=chosen.status,
        latency_ms=http_outcome.latency_ms,
        error=chosen.error,
        kind=chosen.kind,
        meta=meta or None,
    )
