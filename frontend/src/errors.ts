// Maps a raw probe error string to a friendly i18n key with a plain-language
// hint. Returns null when the error is already human-readable (e.g. "expected
// status 200, got 500") or unrecognised — the caller then shows the raw text.
export function classifyError(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const s = raw.toLowerCase();
  if (/errno 101|network is unreachable|no route to host|ehostunreach/.test(s)) return "err.unreachable";
  if (/errno 111|connection refused|refused/.test(s)) return "err.refused";
  if (/errno 104|connection reset|reset by peer/.test(s)) return "err.reset";
  if (/timed out|timeout/.test(s)) return "err.timeout";
  if (/name or service not known|getaddrinfo|errno -[0-9]|nodename|temporary failure in name|name resolution/.test(s))
    return "err.dns";
  if (/certificate has expired|certificate expired|cert expired/.test(s)) return "err.certExpired";
  if (/self.?signed|unable to get local issuer|verify failed|untrusted|not valid for|hostname mismatch|not yet valid/.test(s))
    return "err.certInvalid";
  if (/expected status/.test(s)) return "err.status";
  if (/body substring|body regex|json path/.test(s)) return "err.body";
  if (/ssl|tls|handshake/.test(s)) return "err.tls";
  return null;
}
