from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.orm import Session


# PUBLIC_INTERFACE
def write_audit_log(
    db: Session,
    *,
    request: Optional[Request],
    actor_user_id: Optional[str],
    actor_email: Optional[str],
    action: str,
    entity_type: str,
    entity_id: Optional[str],
    before_data: Optional[Dict[str, Any]] = None,
    after_data: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Append an immutable audit log record to the database."""
    ip_address = None
    user_agent = None
    if request is not None:
        ip_address = request.client.host if request.client else None
        user_agent = request.headers.get("user-agent")

    db.execute(
        text(
            """
            INSERT INTO audit_log (
              actor_user_id, actor_email, action, entity_type, entity_id,
              before_data, after_data, metadata, ip_address, user_agent
            )
            VALUES (
              CAST(:actor_user_id AS uuid), :actor_email, :action, :entity_type,
              CAST(:entity_id AS uuid), CAST(:before_data AS jsonb), CAST(:after_data AS jsonb),
              CAST(:metadata AS jsonb), CAST(:ip_address AS inet), :user_agent
            )
            """
        ),
        {
            "actor_user_id": actor_user_id,
            "actor_email": actor_email,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "before_data": None if before_data is None else before_data,
            "after_data": None if after_data is None else after_data,
            "metadata": {} if metadata is None else metadata,
            "ip_address": ip_address,
            "user_agent": user_agent,
        },
    )
