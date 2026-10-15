"""API-key authentication and role-based scopes.

Keys are configuration, not data: they live in the environment, so a key can be
rotated without a migration. A key maps to a *role* which grants a set of
*scopes*; authorization (may this caller read the audit log?) is enforced by
FastAPI dependencies, while authentication (is this caller known?) is enforced
by the gateway middleware before any handler runs.

The audit trail records a short non-reversible *label* derived from the key,
never the key itself, so an operator can trace a caller without the log becoming
a secret store.
"""

from __future__ import annotations

import enum
import hashlib
import hmac

from nmi.core.config import settings

ANONYMOUS = "anonymous"


class Role(enum.StrEnum):
    """A caller's privilege tier. Higher tiers are supersets of lower ones."""

    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


#: Scopes granted by each role. ``read`` covers every data endpoint, ``audit``
#: unlocks the request log, and ``admin`` is reserved for operational surfaces.
ROLE_SCOPES: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"read"}),
    Role.ANALYST: frozenset({"read", "audit"}),
    Role.ADMIN: frozenset({"read", "audit", "admin"}),
}

#: Flat ``api_keys`` predate roles, so they keep their full access.
DEFAULT_ROLE = Role.ADMIN


class Principal:
    """An authenticated caller: a label, a role and the scopes it grants."""

    __slots__ = ("label", "role", "authenticated", "scopes")

    def __init__(self, label: str, role: Role, authenticated: bool) -> None:
        self.label = label
        self.role = role
        self.authenticated = authenticated
        self.scopes: frozenset[str] = ROLE_SCOPES[role] if authenticated else frozenset()


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


def _coerce_role(name: str) -> Role:
    """Map a configured role name to a role, failing closed to ``viewer``."""
    try:
        return Role(name.strip().lower())
    except ValueError:
        return Role.VIEWER


def resolve_principal(headers) -> Principal:
    """Return the :class:`Principal` for a request.

    When auth is disabled every caller is ``anonymous`` with the admin role and
    is accepted; when it is enabled an unknown or missing key yields an
    unauthenticated viewer. Comparison is constant-time so a caller cannot learn
    a key one byte at a time.
    """
    if not settings.api_auth_enabled:
        return Principal(ANONYMOUS, Role.ADMIN, authenticated=True)
    presented = extract_key(headers)
    if not presented:
        return Principal(ANONYMOUS, Role.VIEWER, authenticated=False)
    for key, role_name in settings.api_key_roles.items():
        if hmac.compare_digest(presented, key):
            return Principal(key_label(key), _coerce_role(role_name), authenticated=True)
    for configured in settings.api_keys:
        if hmac.compare_digest(presented, configured):
            return Principal(key_label(configured), DEFAULT_ROLE, authenticated=True)
    return Principal(ANONYMOUS, Role.VIEWER, authenticated=False)
