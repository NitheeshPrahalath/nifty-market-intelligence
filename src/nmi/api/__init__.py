"""Phase 7 API layer: a typed, cached, rate-limited read API over the platform.

Import ``create_app`` (or the prebuilt ``app``) for the ASGI server; everything
else here is an implementation detail that the tests reach into deliberately.
"""

from __future__ import annotations

from nmi.api.app import app, create_app

API_VERSION = "v1"

__all__ = ["API_VERSION", "app", "create_app"]
