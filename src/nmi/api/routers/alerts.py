"""Alert (notification) history."""

from __future__ import annotations

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from nmi.api import service
from nmi.api.deps import Page, get_db, get_page
from nmi.api.schemas import AlertView
from nmi.api.schemas import Page as PageModel

router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=PageModel[AlertView])
def list_alerts(
    type: Annotated[str | None, Query(description="NotificationType value")] = None,
    severity: Annotated[str | None, Query(description="INFO|WARNING|ERROR")] = None,
    since: Annotated[date | None, Query(description="Only alerts on or after this day")] = None,
    index: Annotated[str | None, Query()] = None,
    symbol: Annotated[str | None, Query()] = None,
    page: Annotated[Page, Depends(get_page)] = None,  # type: ignore[assignment]
    db: Annotated[Session, Depends(get_db)] = None,  # type: ignore[assignment]
) -> dict:
    items, total = service.list_alerts(
        db,
        notification_type=type,
        severity=severity,
        since=since,
        index_code=index,
        symbol=symbol,
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
