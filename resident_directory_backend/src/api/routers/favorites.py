from __future__ import annotations

from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Path, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.audit import write_audit_log
from src.api.db import get_db
from src.api.schemas import FavoriteResidentOut, FavoriteStatusOut, HouseholdOut, ResidentOut
from src.api.security import get_current_user

router = APIRouter(tags=["favorites"])


def _apply_privacy(row: Dict, *, is_admin: bool) -> Dict:
    """
    Apply privacy rules to a resident row.

    For admins: no redaction, no opt-out filtering.
    For non-admin users:
      - phone/email are returned as null when corresponding *_visible=false.
    """
    if is_admin:
        return row

    redacted = dict(row)
    if not row.get("phone_visible", True):
        redacted["phone"] = None
    if not row.get("email_visible", True):
        redacted["email"] = None
    return redacted


def _row_to_resident(row: Dict) -> ResidentOut:
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


def _ensure_target_resident_accessible(
    *, db: Session, resident_id: str, is_admin: bool
) -> Dict:
    """
    Ensure the target resident exists and is visible to the caller.

    Non-admin callers cannot favorite or fetch household views for residents that opted out.
    """
    row = db.execute(
        text(
            """
            SELECT r.id::text AS id, r.first_name, r.last_name, r.unit,
                   r.phone, r.email::text AS email,
                   r.phone_visible, r.email_visible, r.directory_opt_out,
                   r.created_at, r.updated_at
            FROM resident r
            WHERE r.id = CAST(:rid AS uuid) AND r.is_active = true
            """
        ),
        {"rid": resident_id},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Resident not found")

    if not is_admin and bool(row.get("directory_opt_out", False)):
        # Match residents.get_resident behavior: hide opted-out residents.
        raise HTTPException(status_code=404, detail="Resident not found")

    return dict(row)


@router.get(
    "/favorites",
    response_model=List[FavoriteResidentOut],
    summary="List my favorites",
    description="Return the current user's favorited residents (privacy enforced).",
    operation_id="favorites_list",
)
def list_favorites(
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[FavoriteResidentOut]:
    """List favorites for the current user."""
    is_admin = user.get("role") == "admin"

    # For non-admins, exclude residents who opted out.
    extra_filter = "" if is_admin else "AND r.directory_opt_out = false"

    rows = db.execute(
        text(
            f"""
            SELECT f.created_at AS favorited_at,
                   r.id::text AS id, r.first_name, r.last_name, r.unit,
                   r.phone, r.email::text AS email,
                   r.phone_visible, r.email_visible, r.directory_opt_out,
                   r.created_at, r.updated_at
            FROM resident_favorite f
            JOIN resident r ON r.id = f.resident_id
            WHERE f.user_id = CAST(:uid AS uuid)
              AND r.is_active = true
              {extra_filter}
            ORDER BY f.created_at DESC
            LIMIT 500
            """
        ),
        {"uid": user["id"]},
    ).mappings().all()

    out: List[FavoriteResidentOut] = []
    for r in rows:
        rr = dict(r)
        resident = _row_to_resident(_apply_privacy(rr, is_admin=is_admin))
        out.append(FavoriteResidentOut(resident=resident, favoritedAt=rr["favorited_at"]))
    return out


@router.get(
    "/favorites/{resident_id}",
    response_model=FavoriteStatusOut,
    summary="Check favorite status",
    description="Check if a given resident is favorited by the current user.",
    operation_id="favorites_get_status",
)
def get_favorite_status(
    resident_id: str = Path(..., description="Resident UUID"),
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FavoriteStatusOut:
    """Return whether the resident is favorited by the current user."""
    # Ensure resident exists/visible (prevents leaking opted-out ids to non-admins).
    _ensure_target_resident_accessible(db=db, resident_id=resident_id, is_admin=(user.get("role") == "admin"))

    row = db.execute(
        text(
            """
            SELECT 1
            FROM resident_favorite
            WHERE user_id = CAST(:uid AS uuid)
              AND resident_id = CAST(:rid AS uuid)
            """
        ),
        {"uid": user["id"], "rid": resident_id},
    ).first()

    return FavoriteStatusOut(residentId=resident_id, isFavorite=bool(row))


@router.post(
    "/favorites/{resident_id}",
    response_model=FavoriteStatusOut,
    summary="Favorite a resident",
    description="Favorite a resident for the current user.",
    operation_id="favorites_add",
    status_code=status.HTTP_200_OK,
)
def add_favorite(
    resident_id: str = Path(..., description="Resident UUID"),
    request: Request = None,  # type: ignore[assignment]
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FavoriteStatusOut:
    """Add a favorite. Idempotent."""
    is_admin = user.get("role") == "admin"
    _ensure_target_resident_accessible(db=db, resident_id=resident_id, is_admin=is_admin)

    db.execute(
        text(
            """
            INSERT INTO resident_favorite (user_id, resident_id)
            VALUES (CAST(:uid AS uuid), CAST(:rid AS uuid))
            ON CONFLICT DO NOTHING
            """
        ),
        {"uid": user["id"], "rid": resident_id},
    )

    if request is not None:
        write_audit_log(
            db,
            request=request,
            actor_user_id=user["id"],
            actor_email=user["email"],
            action="favorites.add",
            entity_type="resident",
            entity_id=resident_id,
        )

    return FavoriteStatusOut(residentId=resident_id, isFavorite=True)


@router.delete(
    "/favorites/{resident_id}",
    response_model=FavoriteStatusOut,
    summary="Unfavorite a resident",
    description="Remove a resident from the current user's favorites.",
    operation_id="favorites_remove",
)
def remove_favorite(
    resident_id: str = Path(..., description="Resident UUID"),
    request: Request = None,  # type: ignore[assignment]
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FavoriteStatusOut:
    """Remove a favorite. Idempotent."""
    is_admin = user.get("role") == "admin"
    _ensure_target_resident_accessible(db=db, resident_id=resident_id, is_admin=is_admin)

    db.execute(
        text(
            """
            DELETE FROM resident_favorite
            WHERE user_id = CAST(:uid AS uuid)
              AND resident_id = CAST(:rid AS uuid)
            """
        ),
        {"uid": user["id"], "rid": resident_id},
    )

    if request is not None:
        write_audit_log(
            db,
            request=request,
            actor_user_id=user["id"],
            actor_email=user["email"],
            action="favorites.remove",
            entity_type="resident",
            entity_id=resident_id,
        )

    return FavoriteStatusOut(residentId=resident_id, isFavorite=False)


@router.get(
    "/residents/{resident_id}/household",
    response_model=HouseholdOut,
    summary="Get household (same unit) members",
    description=(
        "Return all residents in the same unit as the target resident. "
        "Privacy enforcement: non-admins cannot access opted-out residents."
    ),
    operation_id="resident_get_household",
)
def get_household(
    resident_id: str = Path(..., description="Resident UUID to base household lookup on"),
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> HouseholdOut:
    """Get household members (same unit) for a resident."""
    is_admin = user.get("role") == "admin"
    target = _ensure_target_resident_accessible(db=db, resident_id=resident_id, is_admin=is_admin)
    unit = target["unit"]

    extra_filter = "" if is_admin else "AND r.directory_opt_out = false"

    rows = db.execute(
        text(
            f"""
            SELECT r.id::text AS id, r.first_name, r.last_name, r.unit,
                   r.phone, r.email::text AS email,
                   r.phone_visible, r.email_visible, r.directory_opt_out,
                   r.created_at, r.updated_at
            FROM resident r
            WHERE r.unit = :unit
              AND r.is_active = true
              {extra_filter}
            ORDER BY r.last_name, r.first_name
            LIMIT 200
            """
        ),
        {"unit": unit},
    ).mappings().all()

    members: List[ResidentOut] = []
    for r in rows:
        rr = dict(r)
        members.append(_row_to_resident(_apply_privacy(rr, is_admin=is_admin)))

    return HouseholdOut(unit=unit, members=members)
