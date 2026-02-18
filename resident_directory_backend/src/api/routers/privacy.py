from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.audit import write_audit_log
from src.api.db import get_db
from src.api.schemas import ResidentPrivacySettings, ResidentPrivacySettingsPatch
from src.api.security import require_resident

router = APIRouter(prefix="/resident/privacy", tags=["resident"])


def _row_to_settings(row: Dict[str, Any]) -> ResidentPrivacySettings:
    return ResidentPrivacySettings(
        directoryOptOut=bool(row["directory_opt_out"]),
        phoneVisible=bool(row["phone_visible"]),
        emailVisible=bool(row["email_visible"]),
    )


@router.get(
    "",
    response_model=ResidentPrivacySettings,
    summary="Get my privacy settings",
    description=(
        "Resident-only: get the current resident's directory privacy settings "
        "(directory opt-out + per-field visibility)."
    ),
    operation_id="resident_get_privacy_settings",
)
def get_my_privacy_settings(
    resident_user: Dict = Depends(require_resident),
    db: Session = Depends(get_db),
) -> ResidentPrivacySettings:
    """Get privacy settings for the authenticated resident."""
    if not resident_user.get("resident_id"):
        raise HTTPException(status_code=403, detail="Resident account is not linked to a resident record")

    row = db.execute(
        text(
            """
            SELECT directory_opt_out, phone_visible, email_visible
            FROM resident
            WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": resident_user["resident_id"]},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Resident not found")

    return _row_to_settings(dict(row))


@router.patch(
    "",
    response_model=ResidentPrivacySettings,
    summary="Update my privacy settings",
    description=(
        "Resident-only: update directory privacy settings. "
        "These settings are applied immediately to directory results for non-admin users."
    ),
    operation_id="resident_update_privacy_settings",
)
def update_my_privacy_settings(
    payload: ResidentPrivacySettingsPatch,
    request: Request,
    resident_user: Dict = Depends(require_resident),
    db: Session = Depends(get_db),
) -> ResidentPrivacySettings:
    """Update privacy settings for the authenticated resident."""
    if not resident_user.get("resident_id"):
        raise HTTPException(status_code=403, detail="Resident account is not linked to a resident record")

    before = db.execute(
        text(
            """
            SELECT directory_opt_out, phone_visible, email_visible
            FROM resident
            WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": resident_user["resident_id"]},
    ).mappings().first()

    if not before:
        raise HTTPException(status_code=404, detail="Resident not found")

    updates: Dict[str, Any] = {}
    if payload.directoryOptOut is not None:
        updates["directory_opt_out"] = payload.directoryOptOut
    if payload.phoneVisible is not None:
        updates["phone_visible"] = payload.phoneVisible
    if payload.emailVisible is not None:
        updates["email_visible"] = payload.emailVisible

    if not updates:
        return _row_to_settings(dict(before))

    set_sql = ", ".join([f"{k} = :{k}" for k in updates.keys()])
    updates["rid"] = resident_user["resident_id"]

    row = db.execute(
        text(
            f"""
            UPDATE resident
            SET {set_sql}
            WHERE id = CAST(:rid AS uuid)
            RETURNING directory_opt_out, phone_visible, email_visible
            """
        ),
        updates,
    ).mappings().first()

    write_audit_log(
        db,
        request=request,
        actor_user_id=resident_user["id"],
        actor_email=resident_user["email"],
        action="resident.privacy.update",
        entity_type="resident",
        entity_id=resident_user["resident_id"],
        before_data=dict(before),
        after_data=dict(row) if row else None,
    )

    if not row:
        raise HTTPException(status_code=500, detail="Failed to update privacy settings")

    return _row_to_settings(dict(row))
