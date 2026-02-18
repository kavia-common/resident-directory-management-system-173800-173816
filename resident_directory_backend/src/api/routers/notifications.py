from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.schemas import NotificationMarkReadIn, NotificationMarkReadOut, NotificationOut
from src.api.security import require_resident

router = APIRouter(prefix="/resident/notifications", tags=["resident"])


def _row_to_out(r: Dict[str, Any]) -> NotificationOut:
    return NotificationOut(
        id=r["id"],
        type=r["type"],
        title=r["title"],
        body=r["body"],
        entityType=r.get("entity_type"),
        entityId=r.get("entity_id"),
        isRead=bool(r["is_read"]),
        readAt=r.get("read_at"),
        createdAt=r["created_at"],
    )


@router.get(
    "",
    response_model=List[NotificationOut],
    summary="List my notifications",
    description=(
        "Resident-only: list in-app notifications for the current user (newest first). "
        "Supports filtering by unread/read."
    ),
    operation_id="resident_list_notifications",
)
# PUBLIC_INTERFACE
def list_my_notifications(
    unread_only: bool = Query(
        False,
        description="If true, return only unread notifications.",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max notifications to return"),
    resident_user: Dict = Depends(require_resident),
    db: Session = Depends(get_db),
) -> List[NotificationOut]:
    """List notifications for the current resident user."""
    where = ["n.user_id = CAST(:uid AS uuid)"]
    params: Dict[str, Any] = {"uid": resident_user["id"], "limit": limit}

    if unread_only:
        where.append("n.is_read = false")

    where_sql = " AND ".join(where)
    rows = db.execute(
        text(
            f"""
            SELECT
              n.id::text AS id,
              n.type,
              n.title,
              n.body,
              n.entity_type,
              n.entity_id::text AS entity_id,
              n.is_read,
              n.read_at,
              n.created_at
            FROM app_notification n
            WHERE {where_sql}
            ORDER BY n.created_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()

    return [_row_to_out(dict(r)) for r in rows]


@router.post(
    "/mark-read",
    response_model=NotificationMarkReadOut,
    summary="Mark notifications read",
    description="Resident-only: mark one or more of the current user's notifications as read.",
    operation_id="resident_mark_notifications_read",
)
# PUBLIC_INTERFACE
def mark_notifications_read(
    payload: NotificationMarkReadIn,
    resident_user: Dict = Depends(require_resident),
    db: Session = Depends(get_db),
) -> NotificationMarkReadOut:
    """Mark notifications read for the current resident user."""
    ids = list(dict.fromkeys(payload.notificationIds))  # de-dupe while preserving order
    if not ids:
        raise HTTPException(status_code=422, detail="notificationIds must be non-empty")

    now = datetime.now(timezone.utc)
    result = db.execute(
        text(
            """
            UPDATE app_notification
            SET is_read = true,
                read_at = COALESCE(read_at, :now)
            WHERE user_id = CAST(:uid AS uuid)
              AND id = ANY(CAST(:ids AS uuid[]))
              AND is_read = false
            """
        ),
        {"uid": resident_user["id"], "ids": ids, "now": now},
    )

    # SQLAlchemy returns rowcount for UPDATE
    updated = int(getattr(result, "rowcount", 0) or 0)
    return NotificationMarkReadOut(updated=updated)
