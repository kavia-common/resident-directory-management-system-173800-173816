from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.audit import write_audit_log
from src.api.db import get_db
from src.api.schemas import UpdateRequestCreate, UpdateRequestOut
from src.api.security import require_resident

router = APIRouter(prefix="/resident", tags=["resident"])


@router.get(
    "/update-requests",
    response_model=List[UpdateRequestOut],
    summary="List my update requests",
    description=(
        "Resident-only: list the currently authenticated resident's submitted update requests "
        "(pending/approved/rejected), including review note if available."
    ),
    operation_id="resident_list_my_update_requests",
)
def list_my_update_requests(
    status: str | None = Query(
        None,
        description="Optional status filter: pending/approved/rejected",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max entries to return (newest first)"),
    resident_user: Dict = Depends(require_resident),
    db: Session = Depends(get_db),
) -> List[UpdateRequestOut]:
    """List the current resident user's own update requests."""
    if not resident_user.get("resident_id"):
        raise HTTPException(
            status_code=403, detail="Resident account is not linked to a resident record"
        )

    where = ["rcr.resident_id = CAST(:resident_id AS uuid)"]
    params: Dict[str, Any] = {"resident_id": resident_user["resident_id"], "limit": limit}

    if status:
        status_map = {"pending": "PENDING", "approved": "APPROVED", "rejected": "REJECTED"}
        if status not in status_map:
            raise HTTPException(status_code=422, detail="Invalid status filter")
        where.append("rcr.status = :st")
        params["st"] = status_map[status]

    where_sql = " AND ".join(where)

    rows = db.execute(
        text(
            f"""
            SELECT
              rcr.id::text AS id,
              rcr.resident_id::text AS resident_id,
              rcr.requested_by_user_id::text AS requested_by_user_id,
              rcr.requested_changes,
              rcr.status::text AS status,
              rcr.created_at,
              rcr.reviewed_at,
              rcr.reviewed_by_user_id::text AS reviewed_by_user_id,
              rcr.review_note
            FROM resident_change_request rcr
            WHERE {where_sql}
            ORDER BY rcr.created_at DESC
            LIMIT :limit
            """
        ),
        params,
    ).mappings().all()

    out: List[UpdateRequestOut] = []
    for r in rows:
        out.append(
            UpdateRequestOut(
                id=r["id"],
                residentId=r["resident_id"],
                requestedByUserId=r.get("requested_by_user_id"),
                fields=r["requested_changes"] or {},
                status={"PENDING": "pending", "APPROVED": "approved", "REJECTED": "rejected"}.get(
                    r["status"], "pending"
                ),
                createdAt=r.get("created_at"),
                reviewedAt=r.get("reviewed_at"),
                reviewedByUserId=r.get("reviewed_by_user_id"),
                reviewNote=r.get("review_note"),
            )
        )
    return out


@router.post(
    "/update-requests",
    response_model=UpdateRequestOut,
    summary="Submit update request",
    description="Resident-only: submit a request to update phone/email. Requires admin approval.",
    operation_id="resident_create_update_request",
)
def create_update_request(
    payload: UpdateRequestCreate,
    request: Request,
    resident_user: Dict = Depends(require_resident),
    db: Session = Depends(get_db),
) -> UpdateRequestOut:
    """Create a resident change request for phone/email fields."""
    if not resident_user.get("resident_id"):
        raise HTTPException(status_code=403, detail="Resident account is not linked to a resident record")
    if payload.residentId != resident_user["resident_id"]:
        raise HTTPException(status_code=403, detail="Cannot submit requests for another resident")

    fields: Dict[str, Any] = payload.fields or {}
    allowed = {k: v for k, v in fields.items() if k in ("phone", "email")}
    if not allowed:
        raise HTTPException(status_code=422, detail="Only phone/email fields are allowed")

    row = db.execute(
        text(
            """
            INSERT INTO resident_change_request (
              resident_id, requested_by_user_id, status, requested_changes
            )
            VALUES (
              CAST(:resident_id AS uuid),
              CAST(:user_id AS uuid),
              'PENDING',
              CAST(:changes AS jsonb)
            )
            RETURNING
              id::text AS id,
              resident_id::text AS resident_id,
              requested_by_user_id::text AS requested_by_user_id,
              requested_changes,
              status::text AS status,
              created_at,
              reviewed_at,
              reviewed_by_user_id::text AS reviewed_by_user_id,
              review_note
            """
        ),
        {"resident_id": payload.residentId, "user_id": resident_user["id"], "changes": allowed},
    ).mappings().first()

    write_audit_log(
        db,
        request=request,
        actor_user_id=resident_user["id"],
        actor_email=resident_user["email"],
        action="change_request.create",
        entity_type="resident_change_request",
        entity_id=row["id"],
        after_data=dict(row),
        metadata={"resident_id": payload.residentId},
    )

    return UpdateRequestOut(
        id=row["id"],
        residentId=row["resident_id"],
        requestedByUserId=row.get("requested_by_user_id"),
        fields=row["requested_changes"] or {},
        status="pending",
        createdAt=row.get("created_at"),
        reviewedAt=row.get("reviewed_at"),
        reviewedByUserId=row.get("reviewed_by_user_id"),
        reviewNote=row.get("review_note"),
    )
