from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.audit import write_audit_log
from src.api.db import get_db
from src.api.schemas import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    ResetPasswordRequest,
)
from src.api.security import (
    generate_reset_token,
    get_current_user,
    hash_password,
    hash_reset_token,
    verify_password,
)
from src.api.settings import settings

router = APIRouter(prefix="/auth", tags=["auth"])


def _maybe_get_user_by_email(db: Session, email: str) -> Optional[Dict[str, Any]]:
    row = db.execute(
        text(
            """
            SELECT id::text AS id, email::text AS email, password_hash, is_active
            FROM app_user
            WHERE email = :email
            """
        ),
        {"email": email},
    ).mappings().first()
    return dict(row) if row else None


def _consume_reset_token(db: Session, *, token_hash: str) -> Dict[str, Any]:
    """
    Atomically mark a reset token as used (if valid) and return the token record.

    Raises 400 on invalid/expired/used.
    """
    now = datetime.now(timezone.utc)
    row = db.execute(
        text(
            """
            UPDATE password_reset_token
            SET used_at = :now
            WHERE token_hash = :th
              AND used_at IS NULL
              AND expires_at > :now
            RETURNING
              id::text AS id,
              user_id::text AS user_id,
              token_hash,
              expires_at,
              used_at,
              created_at
            """
        ),
        {"th": token_hash, "now": now},
    ).mappings().first()

    if not row:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    return dict(row)


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Request password reset",
    description=(
        "Request a password reset token. Always returns ok=true (even if email doesn't exist) "
        "to prevent account enumeration."
    ),
    operation_id="auth_forgot_password",
)
# PUBLIC_INTERFACE
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> ForgotPasswordResponse:
    """Create a password reset token for the given email (if it exists)."""
    user = _maybe_get_user_by_email(db, str(payload.email))

    # Always return ok=true to avoid leaking whether the email exists.
    if not user or not user.get("is_active"):
        # Still record a generic audit entry without revealing existence.
        write_audit_log(
            db,
            request=request,
            actor_user_id=None,
            actor_email=str(payload.email),
            action="auth.password_reset.request",
            entity_type="app_user",
            entity_id=None,
            metadata={"email_found": False},
        )
        return ForgotPasswordResponse(ok=True, resetToken=None)

    raw_token = generate_reset_token()
    token_hash = hash_reset_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.password_reset_token_expires_minutes
    )

    # Optional cleanup: remove older unused tokens for this user (keeps table small).
    db.execute(
        text(
            """
            DELETE FROM password_reset_token
            WHERE user_id = CAST(:uid AS uuid) AND used_at IS NULL
            """
        ),
        {"uid": user["id"]},
    )

    db.execute(
        text(
            """
            INSERT INTO password_reset_token (user_id, token_hash, expires_at)
            VALUES (CAST(:uid AS uuid), :th, :exp)
            """
        ),
        {"uid": user["id"], "th": token_hash, "exp": expires_at},
    )

    write_audit_log(
        db,
        request=request,
        actor_user_id=user["id"],
        actor_email=user["email"],
        action="auth.password_reset.request",
        entity_type="app_user",
        entity_id=user["id"],
        metadata={"email_found": True, "expires_minutes": settings.password_reset_token_expires_minutes},
    )

    # In production: send email with link containing raw_token.
    # For this project: return token only when email sending is disabled (dev mode).
    if settings.email_sending_enabled:
        return ForgotPasswordResponse(ok=True, resetToken=None)

    return ForgotPasswordResponse(ok=True, resetToken=raw_token)


@router.post(
    "/reset-password",
    summary="Reset password using token",
    description="Reset account password using a valid, unexpired, single-use reset token.",
    operation_id="auth_reset_password",
)
# PUBLIC_INTERFACE
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Reset password with token + new password."""
    token_hash = hash_reset_token(payload.token)

    token_row = _consume_reset_token(db, token_hash=token_hash)
    user_id = token_row["user_id"]

    # Update password
    new_hash = hash_password(payload.newPassword)
    db.execute(
        text("UPDATE app_user SET password_hash = :ph WHERE id = CAST(:uid AS uuid)"),
        {"ph": new_hash, "uid": user_id},
    )

    write_audit_log(
        db,
        request=request,
        actor_user_id=user_id,
        actor_email=None,
        action="auth.password_reset.complete",
        entity_type="app_user",
        entity_id=user_id,
        metadata={"reset_token_id": token_row["id"]},
    )

    return {"ok": True}


@router.post(
    "/change-password",
    summary="Change password (authenticated)",
    description="Change the current user's password by providing currentPassword + newPassword.",
    operation_id="auth_change_password",
)
# PUBLIC_INTERFACE
def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    user: Dict = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Change password for the currently authenticated user."""
    user_row = db.execute(
        text(
            """
            SELECT id::text AS id, email::text AS email, password_hash, is_active
            FROM app_user
            WHERE id = CAST(:uid AS uuid)
            """
        ),
        {"uid": user["id"]},
    ).mappings().first()

    if not user_row or not user_row["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    if not verify_password(payload.currentPassword, user_row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid current password")

    db.execute(
        text("UPDATE app_user SET password_hash = :ph WHERE id = CAST(:uid AS uuid)"),
        {"ph": hash_password(payload.newPassword), "uid": user["id"]},
    )

    write_audit_log(
        db,
        request=request,
        actor_user_id=user["id"],
        actor_email=user["email"],
        action="auth.password.change",
        entity_type="app_user",
        entity_id=user["id"],
    )

    return {"ok": True}
