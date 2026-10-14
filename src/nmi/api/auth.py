"""API-key authentication.

Keys are configuration, not data: they live in ``settings.api_keys`` (and thus
the environment), so a key can be rotated without a migration. The audit trail
records a short non-reversible *label* derived from the key, never the key
itself, so an operator can trace a caller without the log becoming a secret
store.
"""

from __future__ import annotations

import hashlib
import hmac

from nmi.core.config import settings

ANONYMOUS = "anonymous"


def key_label(key: str) -> str:
    """A stable, non-reversible label for a key (safe to log and store)."""
    return f"key:{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def extract_key(headers) -> str | None:
    """Read the key from ``X-API-Key`` or an ``Authorization: Bearer`` header."""
    api_key = headers.get("x-api-key")
    if api_key:
        return api_key.strip()
    authorization = headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


def resolve_principal(headers) -> tuple[str, bool]:
    """Return ``(principal, authenticated)`` for a request.

    When auth is disabled every caller is ``anonymous`` and accepted; when it is
    enabled an unknown or missing key is rejected. Comparison is constant-time
    so a caller cannot learn a key one byte at a time.
    """
    if not settings.api_auth_enabled:
        return ANONYMOUS, True
    presented = extract_key(headers)
    if not presented:
        return ANONYMOUS, False
    for configured in settings.api_keys:
        if hmac.compare_digest(presented, configured):
            return key_label(configured), True
    return ANONYMOUS, False
