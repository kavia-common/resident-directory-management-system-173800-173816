from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
import hashlib
import secrets
from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.db import get_db
from src.api.settings import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer(auto_error=False)


def _require_secret() -> str:
    if not settings.jwt_secret_key:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Please configure it in the backend .env file."
        )
    return settings.jwt_secret_key


# PUBLIC_INTERFACE
def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt."""
    return pwd_context.hash(password)


# PUBLIC_INTERFACE
def generate_reset_token() -> str:
    """Generate a cryptographically-secure password reset token (URL-safe)."""
    # 32 bytes => ~43 chars base64url; suitable for single-use reset links.
    return secrets.token_urlsafe(32)


# PUBLIC_INTERFACE
def hash_reset_token(token: str) -> str:
    """Hash a password reset token for storage (SHA-256, hex).

    We never store raw reset tokens in the DB.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored hash."""
    return pwd_context.verify(password, password_hash)


def create_access_token(*, sub: str, role: str, resident_id: Optional[str]) -> str:
    """Create a signed JWT access token."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_expires_minutes)
    payload: Dict[str, Any] = {
        "sub": sub,
        "role": role,
        "resident_id": resident_id,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, _require_secret(), algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Dict[str, Any]:
    """Decode and validate JWT token; raises HTTP 401 on failure."""
    try:
        return jwt.decode(
            token,
            _require_secret(),
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False},
        )
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
        ) from e


def _fetch_user_and_role(db: Session, user_id: str) -> Tuple[Dict[str, Any], str, Optional[str]]:
    user_row = db.execute(
        text(
            """
            SELECT u.id::text AS id, u.email::text AS email, u.is_active AS is_active
            FROM app_user u
            WHERE u.id = :user_id
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    if not user_row or not user_row["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or inactive")

    # Assume a single effective role; prefer admin if multiple.
    role_row = db.execute(
        text(
            """
            SELECT r.name::text AS role_name
            FROM user_role ur
            JOIN role r ON r.id = ur.role_id
            WHERE ur.user_id = :user_id
            ORDER BY CASE WHEN r.name='admin' THEN 0 ELSE 1 END
            LIMIT 1
            """
        ),
        {"user_id": user_id},
    ).mappings().first()

    role = role_row["role_name"] if role_row else "resident"

    resident_row = db.execute(
        text("SELECT id::text AS resident_id FROM resident WHERE user_id = :user_id"),
        {"user_id": user_id},
    ).mappings().first()

    resident_id = resident_row["resident_id"] if resident_row else None
    return dict(user_row), role, resident_id


# PUBLIC_INTERFACE
def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """FastAPI dependency to retrieve the current authenticated user."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    payload = decode_token(creds.credentials)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    user, role, resident_id = _fetch_user_and_role(db, user_id)
    user["role"] = role
    user["resident_id"] = resident_id
    return user


def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Dependency that enforces admin role."""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def require_resident(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """Dependency that enforces resident role."""
    if user.get("role") != "resident":
        raise HTTPException(status_code=403, detail="Resident access required")
    return user
