"""API request audit log (admin)."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import Page, get_db, get_page
from nmi.api.schemas import AuditEntryView
from nmi.api.schemas import Page as PageModel

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=PageModel[AuditEntryView])
def list_audit(
    principal: Annotated[str | None, Query(description="Filter by authenticated key label")] = None,
    path: Annotated[str | None, Query(description="Substring match on the request path")] = None,
    status_code: Annotated[int | None, Query(ge=100, le=599)] = None,
    request_id: Annotated[
        str | None, Query(description="Correlate with the X-Request-Id header")
    ] = None,
    since: Annotated[date | None, Query()] = None,
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    """The gateway's record of every request it served."""
    items, total = service.list_audit(
        db,
        principal=principal,
        path=path,
        status_code=status_code,
        since=since,
        request_id=request_id,
        offset=page.offset,
        limit=page.limit,
    )
    pages = (total + page.page_size - 1) // page.page_size if total else 0
    return {
        "items": items,
        "total": total,
        "page": page.page,
        "page_size": page.page_size,
        "pages": pages,
    }
