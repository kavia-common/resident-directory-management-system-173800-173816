from __future__ import annotations

from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.api.settings import settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


# PUBLIC_INTERFACE
def get_engine() -> Engine:
    """Get (and lazily initialize) the SQLAlchemy Engine."""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = create_engine(
            settings.database_dsn(),
            pool_pre_ping=True,
        )
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


@contextmanager
def _session_scope() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    db = _SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


# PUBLIC_INTERFACE
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session."""
    with _session_scope() as session:
        yield session
