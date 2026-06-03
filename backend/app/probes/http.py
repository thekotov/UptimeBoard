import re
import time

import httpx

from app.models.monitoring import STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome


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
      headers              dict of extra request headers
      basic_user/basic_pass   HTTP Basic auth
      bearer_token         Authorization: Bearer <token>
      expected_body_substr substring that must appear in the body
      expected_body_regex  regex that must match the body
      json_path/json_expected  resolve a JSON path and compare to expected (string)
      follow_redirects     bool (default True)
    """
    url = config.get("url") or f"http://{host}"
    method = (config.get("method") or "GET").upper()
    expected_status = int(config.get("expected_status", 200))
    follow_redirects = bool(config.get("follow_redirects", True))

    headers = dict(config.get("headers") or {})
    if config.get("bearer_token"):
        headers["Authorization"] = f"Bearer {config['bearer_token']}"
    auth = None
    if config.get("basic_user"):
        auth = (config["basic_user"], config.get("basic_pass", ""))

    start = time.perf_counter()
    with httpx.Client(timeout=timeout_sec, follow_redirects=follow_redirects) as client:
        resp = client.request(method, url, headers=headers or None, auth=auth)
    latency_ms = (time.perf_counter() - start) * 1000

    def down(msg: str) -> ProbeOutcome:
        return ProbeOutcome(status=STATUS_DOWN, latency_ms=latency_ms, error=msg)

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

    return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms)
