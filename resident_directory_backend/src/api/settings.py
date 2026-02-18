from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Optional


def _split_csv(value: str) -> List[str]:
    return [v.strip() for v in value.split(",") if v.strip()]


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    # Service metadata
    app_title: str = "Resident Directory Backend"
    app_description: str = (
        "Backend API for resident directory management with JWT auth, "
        "role-based access, CSV import/export, approval workflow, and audit logging."
    )
    app_version: str = "1.0.0"

    # CORS
    allowed_origins: List[str] = None  # type: ignore[assignment]
    allowed_methods: List[str] = None  # type: ignore[assignment]
    allowed_headers: List[str] = None  # type: ignore[assignment]

    # Security
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expires_minutes: int = 60 * 24  # 24h

    # Database
    postgres_url: str = ""
    postgres_user: Optional[str] = None
    postgres_password: Optional[str] = None
    postgres_db: Optional[str] = None
    postgres_port: Optional[str] = None

    trust_proxy: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "allowed_origins",
            _split_csv(os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")),
        )
        object.__setattr__(
            self,
            "allowed_methods",
            _split_csv(os.getenv("ALLOWED_METHODS", "GET,POST,PUT,DELETE,PATCH,OPTIONS")),
        )
        object.__setattr__(
            self,
            "allowed_headers",
            _split_csv(os.getenv("ALLOWED_HEADERS", "Content-Type,Authorization")),
        )

        secret = os.getenv("JWT_SECRET_KEY") or os.getenv("SECRET_KEY") or ""
        object.__setattr__(self, "jwt_secret_key", secret)

        object.__setattr__(self, "jwt_algorithm", os.getenv("JWT_ALGORITHM", "HS256"))
        object.__setattr__(
            self,
            "access_token_expires_minutes",
            int(os.getenv("ACCESS_TOKEN_EXPIRES_MINUTES", str(60 * 24))),
        )

        object.__setattr__(self, "postgres_url", os.getenv("POSTGRES_URL", ""))

        object.__setattr__(self, "postgres_user", os.getenv("POSTGRES_USER"))
        object.__setattr__(self, "postgres_password", os.getenv("POSTGRES_PASSWORD"))
        object.__setattr__(self, "postgres_db", os.getenv("POSTGRES_DB"))
        object.__setattr__(self, "postgres_port", os.getenv("POSTGRES_PORT"))

        object.__setattr__(
            self,
            "trust_proxy",
            (os.getenv("TRUST_PROXY", "false").lower() in ("1", "true", "yes", "on")),
        )

    def database_dsn(self) -> str:
        """Return a SQLAlchemy DSN string for PostgreSQL."""
        if self.postgres_url:
            # POSTGRES_URL from DB container is like: postgresql://localhost:5000/myapp
            # SQLAlchemy sync dialect uses postgresql+psycopg://
            if self.postgres_url.startswith("postgresql+"):
                return self.postgres_url
            if self.postgres_url.startswith("postgresql://"):
                return "postgresql+psycopg://" + self.postgres_url[len("postgresql://") :]
            return self.postgres_url

        # Fallback to assembling from individual vars if present.
        # (Do not assume; only build if enough pieces exist)
        if self.postgres_user and self.postgres_password and self.postgres_db and self.postgres_port:
            return (
                f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
                f"@localhost:{self.postgres_port}/{self.postgres_db}"
            )

        raise RuntimeError(
            "Database is not configured. Set POSTGRES_URL (preferred) or "
            "POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB/POSTGRES_PORT."
        )


settings = Settings()
