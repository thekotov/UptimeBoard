import hashlib
import socket
import ssl
import time
from datetime import datetime, timezone

from app.models.monitoring import STATUS_DEGRADED, STATUS_DOWN, STATUS_UP
from app.probes.base import ProbeOutcome, is_timeout_error


def execute(host: str, config: dict, timeout_sec: int) -> ProbeOutcome:
    """TLS certificate probe — checks reachability, trust and certificate expiry.

    config keys:
      port        TLS port (default 443)
      warn_days   mark "degraded" when fewer than this many days remain (default 14)
    """
    port = int(config.get("port", 443))
    warn_days = int(config.get("warn_days", 14))
    return check_certificate(host, port, timeout_sec, warn_days)


def check_certificate(host: str, port: int, timeout_sec: int, warn_days: int = 14) -> ProbeOutcome:
    """Inspect the TLS certificate served at ``host:port`` and judge its health.

    Reused by the standalone TLS probe and by the HTTP probe's optional
    certificate tracking. The happy path does a single verified handshake. On a
    verification failure (expired / hostname mismatch / self-signed / untrusted
    CA) the error is classified and a second *unverified* handshake fetches the
    certificate so its metadata can still be reported. ``outcome.meta`` carries
    that metadata when available: {expires_at, not_before, issuer, subject, sans,
    fingerprint}. ``fingerprint`` is the SHA-256 of the DER certificate, used to
    detect when the served certificate changes.
    """
    ctx = ssl.create_default_context()
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout_sec) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
                der = ssock.getpeercert(binary_form=True)
        latency_ms = (time.perf_counter() - start) * 1000
    except ssl.SSLCertVerificationError as exc:
        # Reachable, but the certificate is not trustworthy. Classify the reason
        # and still pull metadata (best-effort) from an unverified handshake.
        latency_ms = (time.perf_counter() - start) * 1000
        status, error = _classify_verify_error(exc, host)
        meta = _meta_insecure(host, port, timeout_sec)
        error = _enrich_expiry(error, meta)
        return ProbeOutcome(status=status, latency_ms=latency_ms, error=error, meta=meta)
    except (OSError, ssl.SSLError) as exc:
        return ProbeOutcome(
            status=STATUS_DOWN, error=str(exc),
            kind="timeout" if is_timeout_error(exc) else None,
        )

    meta = _meta_from_peercert(cert)
    meta["fingerprint"] = _fingerprint(der)
    expires = _parse_cert_time(cert.get("notAfter"))
    if expires is None:
        return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms, meta=meta)

    days_left = (expires - datetime.now(timezone.utc)).days
    if days_left < 0:
        # Verification normally rejects expired certs before we get here; kept as
        # a guard in case the platform trust store is lenient.
        return ProbeOutcome(status=STATUS_DOWN, latency_ms=latency_ms,
                            error=f"certificate expired {-days_left}d ago", meta=meta)
    if days_left < warn_days:
        return ProbeOutcome(status=STATUS_DEGRADED, latency_ms=latency_ms,
                            error=f"certificate expires in {days_left}d", meta=meta)
    return ProbeOutcome(status=STATUS_UP, latency_ms=latency_ms, meta=meta)


# ---- certificate parsing helpers ----


def _fingerprint(der: bytes | None) -> str | None:
    """SHA-256 of the DER-encoded certificate — a stable identity that changes
    whenever the served certificate is replaced (renewal, reissue, swap)."""
    return hashlib.sha256(der).hexdigest() if der else None


def _parse_cert_time(value: str | None) -> datetime | None:
    """Parse OpenSSL's ``getpeercert`` time format ('Jun  1 12:00:00 2026 GMT')."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _name_field(name, key: str) -> str | None:
    """Pull a field (e.g. commonName) from a getpeercert issuer/subject structure,
    which is a tuple of relative distinguished names, each a tuple of (key, value)."""
    for rdn in name or ():
        for k, v in rdn:
            if k == key:
                return v
    return None


def _format_issuer(org: str | None, cn: str | None) -> str | None:
    if org and cn and org != cn:
        return f"{org} ({cn})"
    return org or cn


def _meta_from_peercert(cert: dict) -> dict:
    """Build the metadata dict from a verified ``getpeercert()`` result."""
    expires = _parse_cert_time(cert.get("notAfter"))
    not_before = _parse_cert_time(cert.get("notBefore"))
    sans = [v for typ, v in cert.get("subjectAltName", ()) if typ == "DNS"]
    return {
        "expires_at": expires.isoformat() if expires else None,
        "not_before": not_before.isoformat() if not_before else None,
        "issuer": _format_issuer(
            _name_field(cert.get("issuer"), "organizationName"),
            _name_field(cert.get("issuer"), "commonName"),
        ),
        "subject": _name_field(cert.get("subject"), "commonName"),
        "sans": sans,
    }


def _meta_insecure(host: str, port: int, timeout_sec: int) -> dict:
    """Fetch and parse the certificate without verification, so metadata is still
    available for an untrusted/expired cert. Best-effort: returns {} on any error
    (including when the ``cryptography`` parser is unavailable)."""
    try:
        ctx = ssl._create_unverified_context()  # noqa: SLF001 - intentional: read-only metadata fetch
        with socket.create_connection((host, port), timeout=timeout_sec) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ssock:
                der = ssock.getpeercert(binary_form=True)
        meta = _meta_from_der(der)
        # Fingerprint needs only hashlib, so attach it even if the x509 parser is
        # unavailable and the rest of the metadata came back empty.
        fp = _fingerprint(der)
        if fp:
            meta["fingerprint"] = fp
        return meta
    except Exception:  # noqa: BLE001 - metadata is best-effort; never fail the probe over it
        return {}


def _meta_from_der(der: bytes | None) -> dict:
    """Parse a DER-encoded certificate into the metadata dict via ``cryptography``."""
    if not der:
        return {}
    try:
        from cryptography import x509
        from cryptography.x509.oid import ExtensionOID, NameOID
    except Exception:  # noqa: BLE001 - parser optional; degrade to no metadata
        return {}
    try:
        cert = x509.load_der_x509_certificate(der)

        def _attr(name, oid):
            attrs = name.get_attributes_for_oid(oid)
            return attrs[0].value if attrs else None

        sans: list[str] = []
        try:
            ext = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
            sans = ext.value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            pass

        # Prefer the tz-aware accessors (cryptography >= 42); fall back to the
        # naive UTC ones on older versions.
        try:
            expires = cert.not_valid_after_utc
            not_before = cert.not_valid_before_utc
        except AttributeError:
            expires = cert.not_valid_after.replace(tzinfo=timezone.utc)
            not_before = cert.not_valid_before.replace(tzinfo=timezone.utc)

        return {
            "expires_at": expires.isoformat(),
            "not_before": not_before.isoformat(),
            "issuer": _format_issuer(
                _attr(cert.issuer, NameOID.ORGANIZATION_NAME),
                _attr(cert.issuer, NameOID.COMMON_NAME),
            ),
            "subject": _attr(cert.subject, NameOID.COMMON_NAME),
            "sans": sans,
        }
    except Exception:  # noqa: BLE001 - malformed cert; degrade to no metadata
        return {}


# ---- error classification ----

# OpenSSL X509_V_ERR_* codes we map to friendly reasons.
_VERIFY_EXPIRED = 10
_VERIFY_NOT_YET_VALID = 9
_VERIFY_HOSTNAME_MISMATCH = 62
_VERIFY_SELF_SIGNED = {18, 19}
_VERIFY_UNTRUSTED = {2, 20, 21}


def _classify_verify_error(exc: ssl.SSLCertVerificationError, host: str) -> tuple[str, str]:
    """Map a certificate verification failure to (status, human reason)."""
    code = getattr(exc, "verify_code", None)
    msg = getattr(exc, "verify_message", None) or str(exc)
    low = msg.lower()

    if code == _VERIFY_EXPIRED or "expired" in low:
        return STATUS_DOWN, "certificate expired"
    if code == _VERIFY_NOT_YET_VALID or "not yet valid" in low:
        return STATUS_DOWN, "certificate not yet valid"
    if code == _VERIFY_HOSTNAME_MISMATCH or "hostname mismatch" in low or "doesn't match" in low:
        return STATUS_DOWN, f"hostname mismatch: certificate not valid for {host}"
    if code in _VERIFY_SELF_SIGNED or "self signed" in low or "self-signed" in low:
        return STATUS_DOWN, "self-signed certificate (not trusted)"
    if code in _VERIFY_UNTRUSTED or "unable to get local issuer" in low or "unable to verify" in low:
        return STATUS_DOWN, "untrusted certificate (issuer CA not trusted)"
    return STATUS_DOWN, f"certificate verification failed: {msg}"


def _enrich_expiry(error: str, meta: dict) -> str:
    """If the failure is an expiry and we know the date, add the day count."""
    exp = meta.get("expires_at")
    if "expired" not in error or not exp:
        return error
    try:
        days = (datetime.now(timezone.utc) - datetime.fromisoformat(exp)).days
    except ValueError:
        return error
    return f"certificate expired {days}d ago" if days >= 0 else error
