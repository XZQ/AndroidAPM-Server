"""Check a public TLS certificate's remaining validity from outside the host.

The cloud deployment uses short-lived certificates issued to an IP address with
HTTP-01 unavailable, so an external expiry probe is the only early warning that
does not depend on shell access. No private data or raw exception text is emitted.
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
from datetime import UTC, datetime

DEFAULT_MIN_DAYS = 7.0
SECONDS_PER_DAY = 86_400.0


def remaining_days(not_after: str, now: datetime) -> float:
    """Return fractional days until the certificate expires (negative when expired)."""
    expiry = datetime.fromtimestamp(ssl.cert_time_to_seconds(not_after), tz=UTC)
    return (expiry - now).total_seconds() / SECONDS_PER_DAY


def fetch_not_after(host: str, port: int, timeout: float) -> str:
    """Return the peer certificate's notAfter value using full TLS verification."""
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls:
            certificate = tls.getpeercert()
    value = certificate.get("notAfter")
    if not isinstance(value, str) or not value:
        raise ValueError("peer certificate has no notAfter field")
    return value


def main() -> int:
    """Return non-zero when the certificate is expiring soon or cannot be verified."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Public hostname or IP to probe.")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--min-days", type=float, default=DEFAULT_MIN_DAYS)
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    try:
        not_after = fetch_not_after(args.host, args.port, args.timeout)
        days = remaining_days(not_after, datetime.now(UTC))
    except (OSError, ssl.SSLError, ValueError) as error:
        # Only the failure category is safe to log; exception text may contain host details.
        print(json.dumps({"host": args.host, "ok": False, "error": type(error).__name__}))
        return 1

    ok = days >= args.min_days
    print(
        json.dumps(
            {
                "host": args.host,
                "ok": ok,
                "not_after": not_after,
                "days_remaining": round(days, 2),
                "min_days": args.min_days,
            }
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
