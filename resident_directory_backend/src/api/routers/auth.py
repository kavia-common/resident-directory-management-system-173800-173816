from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.audit import write_audit_log
from src.api.db import get_db
from src.api.schemas import AuthUser, LoginRequest, TokenResponse
from src.api.security import create_access_token, get_current_user, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _bootstrap_dev_admin_password_if_needed(db: Session) -> None:
    """
    The DB seed sets a placeholder password_hash for admin@example.com.
    To keep the app usable in dev, if that placeholder is present, we set it to
    a real bcrypt hash for password 'admin123'.

    This keeps migrations simple while ensuring secure hashing in runtime.
    """
    row = db.execute(
        text("SELECT password_hash FROM app_user WHERE email='admin@example.com'")
    ).mappings().first()
    if not row:
        return
    if row["password_hash"] == "DEV_ONLY_REPLACE_WITH_REAL_HASH":
        from src.api.security import hash_password

        db.execute(
            text(
                "UPDATE app_user SET password_hash=:ph WHERE email='admin@example.com'"
            ),
            {"ph": hash_password("admin123")},
        )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login (JWT)",
    description="Authenticate a user and receive a JWT token.",
    operation_id="auth_login",
)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    """Authenticate user by email/password and return JWT + user."""
    _bootstrap_dev_admin_password_if_needed(db)

    user_row = db.execute(
        text(
            """
            SELECT id::text AS id, email::text AS email, password_hash, is_active
            FROM app_user
            WHERE email = :email
            """
        ),
        {"email": str(payload.email)},
    ).mappings().first()

    if not user_row or not user_row["is_active"]:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not verify_password(payload.password, user_row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    # pick role
    role_row = db.execute(
        text(
            """
            SELECT r.name::text AS role_name
            FROM user_role ur
            JOIN role r ON r.id = ur.role_id
            WHERE ur.user_id = CAST(:user_id AS uuid)
            ORDER BY CASE WHEN r.name='admin' THEN 0 ELSE 1 END
            LIMIT 1
            """
        ),
        {"user_id": user_row["id"]},
    ).mappings().first()
    role = role_row["role_name"] if role_row else "resident"

    resident_row = db.execute(
        text("SELECT id::text AS resident_id FROM resident WHERE user_id = CAST(:user_id AS uuid)"),
        {"user_id": user_row["id"]},
    ).mappings().first()
    resident_id = resident_row["resident_id"] if resident_row else None

    token = create_access_token(sub=user_row["id"], role=role, resident_id=resident_id)

    db.execute(
        text("UPDATE app_user SET last_login_at = :ts WHERE id = CAST(:user_id AS uuid)"),
        {"ts": datetime.now(timezone.utc), "user_id": user_row["id"]},
    )

    write_audit_log(
        db,
        request=request,
        actor_user_id=user_row["id"],
        actor_email=user_row["email"],
        action="auth.login",
        entity_type="app_user",
        entity_id=user_row["id"],
        metadata={"role": role},
    )

    return TokenResponse(
        token=token,
        user=AuthUser(id=user_row["id"], email=user_row["email"], role=role, residentId=resident_id),
    )


@router.get(
    "/me",
    response_model=AuthUser,
    summary="Current user",
    description="Return the currently authenticated user's identity and role.",
    operation_id="auth_me",
)
def me(user: Dict = Depends(get_current_user)) -> AuthUser:
    """Return the current authenticated user."""
    return AuthUser(
        id=user["id"],
        email=user["email"],
        role=user["role"],
        residentId=user.get("resident_id"),
    )


@router.post(
    "/logout",
    summary="Logout",
    description="Logout endpoint (stateless JWT). Provided for audit consistency.",
    operation_id="auth_logout",
)
def logout(request: Request, user: Dict = Depends(get_current_user), db: Session = Depends(get_db)) -> Dict:
    """Logout (no server-side session), but records an audit log entry."""
    write_audit_log(
        db,
        request=request,
        actor_user_id=user["id"],
        actor_email=user["email"],
        action="auth.logout",
        entity_type="app_user",
        entity_id=user["id"],
    )
    return {"ok": True}
