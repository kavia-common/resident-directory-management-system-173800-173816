from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.schemas import ResidentOut
from src.api.security import get_current_user

router = APIRouter(prefix="/residents", tags=["residents"])


def _apply_privacy(row, *, is_admin: bool):
    """
    Apply privacy rules to a resident row.

    For admins: no redaction, no opt-out filtering.
    For non-admin users:
      - Residents with directory_opt_out=true are excluded by query (list) and redacted (get).
      - phone/email are returned as null when corresponding *_visible=false.
    """
    if is_admin:
        return row

    # Redact fields (note: list endpoint already filters opt-out; get endpoint uses this too)
    redacted = dict(row)
    if not row.get("phone_visible", True):
        redacted["phone"] = None
    if not row.get("email_visible", True):
        redacted["email"] = None
    return redacted


def _split_name(full_name: str) -> Tuple[str, str]:
    parts = [p for p in full_name.strip().split(" ") if p]
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="Name must include first and last name")
    return parts[0], " ".join(parts[1:])


def _row_to_resident(row) -> ResidentOut:
    name = f"{row['first_name']} {row['last_name']}".strip()
    return ResidentOut(
        id=row["id"],
        name=name,
        unit=row["unit"],
        phone=row.get("phone"),
        email=row.get("email"),
        createdAt=row.get("created_at"),
        updatedAt=row.get("updated_at"),
    )


@router.get(
    "",
    response_model=List[ResidentOut],
    summary="List residents (search/filter)",
    description="List residents, searchable by name (q) and filterable by unit.",
    operation_id="list_residents",
)
def list_residents(
    q: Optional[str] = Query(None, description="Search query (name)"),
    unit: Optional[str] = Query(None, description="Unit filter"),
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[ResidentOut]:
    """List residents for authenticated users."""
    is_admin = user.get("role") == "admin"

    where = ["r.is_active = true"]
    params = {}

    # Privacy enforcement: only non-admin users are subject to directory opt-out
    if not is_admin:
        where.append("r.directory_opt_out = false")

    if unit:
        where.append("r.unit ILIKE :unit")
        params["unit"] = unit

    if q:
        where.append("(r.full_name ILIKE :q OR r.unit ILIKE :q)")
        params["q"] = f"%{q}%"

    sql = f"""
        SELECT r.id::text AS id, r.first_name, r.last_name, r.unit,
               r.phone, r.email::text AS email,
               r.phone_visible, r.email_visible, r.directory_opt_out,
               r.created_at, r.updated_at
        FROM resident r
        WHERE {" AND ".join(where)}
        ORDER BY r.last_name, r.first_name
        LIMIT 200
    """

    rows = db.execute(text(sql), params).mappings().all()
    out: List[ResidentOut] = []
    for r in rows:
        out.append(_row_to_resident(_apply_privacy(r, is_admin=is_admin)))
    return out


@router.get(
    "/{resident_id}",
    response_model=ResidentOut,
    summary="Get resident",
    description="Get a single resident record by id.",
    operation_id="get_resident",
)
def get_resident(
    resident_id: str,
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ResidentOut:
    """Get a single resident record."""
    is_admin = user.get("role") == "admin"

    row = db.execute(
        text(
            """
            SELECT r.id::text AS id, r.first_name, r.last_name, r.unit,
                   r.phone, r.email::text AS email,
                   r.phone_visible, r.email_visible, r.directory_opt_out,
                   r.created_at, r.updated_at
            FROM resident r
            WHERE r.id = CAST(:rid AS uuid)
            """
        ),
        {"rid": resident_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Resident not found")

    # Privacy enforcement: if resident opted out, non-admins should not be able to fetch it.
    if not is_admin and row.get("directory_opt_out", False):
        raise HTTPException(status_code=404, detail="Resident not found")

    return _row_to_resident(_apply_privacy(row, is_admin=is_admin))
