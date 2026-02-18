from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.api.audit import write_audit_log
from src.api.db import get_db
from src.api.schemas import (
    AuditLogOut,
    CsvImportError,
    CsvImportResult,
    ResidentCreate,
    ResidentOut,
    ResidentPatch,
    ReviewDecisionIn,
    UpdateRequestOut,
)
from src.api.security import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _split_name(full_name: str) -> Tuple[str, str]:
    parts = [p for p in full_name.strip().split(" ") if p]
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="Name must include first and last name")
    return parts[0], " ".join(parts[1:])


def _resident_row_to_out(r) -> ResidentOut:
    return ResidentOut(
        id=r["id"],
        name=f"{r['first_name']} {r['last_name']}".strip(),
        unit=r["unit"],
        phone=r.get("phone"),
        email=r.get("email"),
        createdAt=r.get("created_at"),
        updatedAt=r.get("updated_at"),
    )


@router.post(
    "/residents",
    response_model=ResidentOut,
    summary="Create resident",
    description="Admin-only: create a resident profile.",
    operation_id="admin_create_resident",
)
def create_resident(
    payload: ResidentCreate,
    request: Request,
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ResidentOut:
    """Create a new resident."""
    first_name, last_name = _split_name(payload.name)

    row = db.execute(
        text(
            """
            INSERT INTO resident (first_name, last_name, unit, phone, email, is_active)
            VALUES (:fn, :ln, :unit, :phone, :email, true)
            RETURNING id::text AS id, first_name, last_name, unit, phone, email::text AS email, created_at, updated_at
            """
        ),
        {
            "fn": first_name,
            "ln": last_name,
            "unit": payload.unit,
            "phone": payload.phone,
            "email": str(payload.email) if payload.email else None,
        },
    ).mappings().first()

    write_audit_log(
        db,
        request=request,
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action="resident.create",
        entity_type="resident",
        entity_id=row["id"],
        after_data=dict(row),
    )

    return _resident_row_to_out(row)


@router.patch(
    "/residents/{resident_id}",
    response_model=ResidentOut,
    summary="Update resident",
    description="Admin-only: patch a resident profile.",
    operation_id="admin_update_resident",
)
def update_resident(
    resident_id: str,
    payload: ResidentPatch,
    request: Request,
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> ResidentOut:
    """Update resident fields."""
    before = db.execute(
        text(
            """
            SELECT id::text AS id, first_name, last_name, unit, phone, email::text AS email, is_active,
                   created_at, updated_at
            FROM resident
            WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": resident_id},
    ).mappings().first()

    if not before:
        raise HTTPException(status_code=404, detail="Resident not found")

    updates: Dict[str, Any] = {}
    if payload.name is not None:
        fn, ln = _split_name(payload.name)
        updates["first_name"] = fn
        updates["last_name"] = ln
    if payload.unit is not None:
        updates["unit"] = payload.unit
    if payload.phone is not None:
        updates["phone"] = payload.phone
    if payload.email is not None:
        updates["email"] = str(payload.email)
    if payload.isActive is not None:
        updates["is_active"] = payload.isActive

    if not updates:
        # no-op
        return _resident_row_to_out(before)

    set_sql = ", ".join([f"{k} = :{k}" for k in updates.keys()])
    updates["rid"] = resident_id

    row = db.execute(
        text(
            f"""
            UPDATE resident
            SET {set_sql}
            WHERE id = CAST(:rid AS uuid)
            RETURNING id::text AS id, first_name, last_name, unit, phone, email::text AS email, created_at, updated_at
            """
        ),
        updates,
    ).mappings().first()

    write_audit_log(
        db,
        request=request,
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action="resident.update",
        entity_type="resident",
        entity_id=resident_id,
        before_data=dict(before),
        after_data=dict(row),
        metadata={"updated_fields": list(updates.keys())},
    )

    return _resident_row_to_out(row)


@router.delete(
    "/residents/{resident_id}",
    summary="Delete resident",
    description="Admin-only: soft-delete a resident (set is_active=false).",
    operation_id="admin_delete_resident",
)
def delete_resident(
    resident_id: str,
    request: Request,
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Soft-delete resident."""
    before = db.execute(
        text(
            """
            SELECT id::text AS id, first_name, last_name, unit, phone, email::text AS email, is_active
            FROM resident WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": resident_id},
    ).mappings().first()
    if not before:
        raise HTTPException(status_code=404, detail="Resident not found")

    db.execute(
        text("UPDATE resident SET is_active = false WHERE id = CAST(:rid AS uuid)"),
        {"rid": resident_id},
    )

    write_audit_log(
        db,
        request=request,
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action="resident.delete",
        entity_type="resident",
        entity_id=resident_id,
        before_data=dict(before),
        after_data={"is_active": False},
    )
    return {"ok": True}


@router.get(
    "/residents/export",
    summary="Export residents CSV",
    description="Admin-only: export active residents to CSV.",
    operation_id="admin_export_residents_csv",
)
def export_residents_csv(
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Export residents as CSV."""
    rows = db.execute(
        text(
            """
            SELECT first_name, last_name, unit, phone, email::text AS email
            FROM resident
            WHERE is_active = true
            ORDER BY last_name, first_name
            """
        )
    ).mappings().all()

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=["name", "unit", "phone", "email"])
    writer.writeheader()
    for r in rows:
        writer.writerow(
            {
                "name": f"{r['first_name']} {r['last_name']}".strip(),
                "unit": r["unit"],
                "phone": r.get("phone") or "",
                "email": r.get("email") or "",
            }
        )

    out.seek(0)
    return StreamingResponse(
        iter([out.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="residents.csv"'},
    )


@router.post(
    "/residents/import",
    response_model=CsvImportResult,
    summary="Import residents CSV",
    description="Admin-only: import residents from CSV with columns: name, unit, phone, email. Upserts by (unit, first_name, last_name).",
    operation_id="admin_import_residents_csv",
)
def import_residents_csv(
    request: Request,
    file: UploadFile = File(..., description="CSV file"),
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> CsvImportResult:
    """Import residents from CSV."""
    content = file.file.read()
    try:
        text_data = content.decode("utf-8-sig")
    except Exception:
        text_data = content.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text_data))
    required = {"name", "unit"}
    if not reader.fieldnames or not required.issubset(set([h.strip() for h in reader.fieldnames])):
        raise HTTPException(status_code=400, detail="CSV must include headers: name, unit, phone, email")

    imported = 0
    updated = 0
    errors: List[CsvImportError] = []

    for idx, row in enumerate(reader, start=1):
        try:
            name = (row.get("name") or "").strip()
            unit = (row.get("unit") or "").strip()
            if not name or not unit:
                raise ValueError("Missing name or unit")

            fn, ln = _split_name(name)
            phone = (row.get("phone") or "").strip() or None
            email = (row.get("email") or "").strip() or None

            # Find existing by natural key
            existing = db.execute(
                text(
                    """
                    SELECT id::text AS id
                    FROM resident
                    WHERE unit = :unit AND first_name = :fn AND last_name = :ln
                    LIMIT 1
                    """
                ),
                {"unit": unit, "fn": fn, "ln": ln},
            ).mappings().first()

            if existing:
                db.execute(
                    text(
                        """
                        UPDATE resident
                        SET phone = :phone, email = :email, is_active = true
                        WHERE id = CAST(:rid AS uuid)
                        """
                    ),
                    {"phone": phone, "email": email, "rid": existing["id"]},
                )
                updated += 1
            else:
                db.execute(
                    text(
                        """
                        INSERT INTO resident (first_name, last_name, unit, phone, email, is_active)
                        VALUES (:fn, :ln, :unit, :phone, :email, true)
                        """
                    ),
                    {"fn": fn, "ln": ln, "unit": unit, "phone": phone, "email": email},
                )
                imported += 1

        except Exception as e:
            errors.append(CsvImportError(row=idx, message=str(e)))

    write_audit_log(
        db,
        request=request,
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action="resident.csv_import",
        entity_type="resident",
        entity_id=None,
        metadata={"filename": file.filename, "imported": imported, "updated": updated, "errors": len(errors)},
    )

    return CsvImportResult(imported=imported, updated=updated, errors=errors)


@router.get(
    "/update-requests",
    response_model=List[UpdateRequestOut],
    summary="List update requests",
    description="Admin-only: list resident change requests (filter by status).",
    operation_id="admin_list_update_requests",
)
def list_update_requests(
    status: Optional[str] = Query(None, description="Filter status: pending/approved/rejected"),
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> List[UpdateRequestOut]:
    """List resident change requests."""
    where = []
    params: Dict[str, Any] = {}
    if status:
        status_map = {"pending": "PENDING", "approved": "APPROVED", "rejected": "REJECTED"}
        if status not in status_map:
            raise HTTPException(status_code=422, detail="Invalid status filter")
        where.append("rcr.status = :st")
        params["st"] = status_map[status]
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

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
            {where_sql}
            ORDER BY rcr.created_at DESC
            LIMIT 200
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
                fields=r["requested_changes"],
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
    "/update-requests/{request_id}",
    response_model=UpdateRequestOut,
    summary="Review update request",
    description="Admin-only: approve or reject a resident update request. Approval applies requested phone/email changes.",
    operation_id="admin_review_update_request",
)
def review_update_request(
    request_id: str,
    payload: ReviewDecisionIn,
    request: Request,
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> UpdateRequestOut:
    """Approve/reject a pending request and apply changes on approval."""
    rcr = db.execute(
        text(
            """
            SELECT id::text AS id, resident_id::text AS resident_id,
                   requested_by_user_id::text AS requested_by_user_id,
                   requested_changes, status::text AS status,
                   created_at, reviewed_at, reviewed_by_user_id::text AS reviewed_by_user_id, review_note
            FROM resident_change_request
            WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": request_id},
    ).mappings().first()

    if not rcr:
        raise HTTPException(status_code=404, detail="Request not found")
    if rcr["status"] != "PENDING":
        raise HTTPException(status_code=409, detail="Request is not pending")

    decision = payload.decision
    new_status = "APPROVED" if decision == "approved" else "REJECTED"
    now = datetime.now(timezone.utc)

    # Apply changes if approved (only phone/email allowed)
    fields: Dict[str, Any] = rcr["requested_changes"] or {}
    allowed = {k: v for k, v in fields.items() if k in ("phone", "email")}
    if decision == "approved" and allowed:
        before_resident = db.execute(
            text(
                "SELECT id::text AS id, phone, email::text AS email FROM resident WHERE id = CAST(:rid AS uuid)"
            ),
            {"rid": rcr["resident_id"]},
        ).mappings().first()

        db.execute(
            text(
                """
                UPDATE resident
                SET phone = COALESCE(:phone, phone),
                    email = COALESCE(:email, email)
                WHERE id = CAST(:rid AS uuid)
                """
            ),
            {"phone": allowed.get("phone"), "email": allowed.get("email"), "rid": rcr["resident_id"]},
        )

        after_resident = db.execute(
            text(
                "SELECT id::text AS id, phone, email::text AS email FROM resident WHERE id = CAST(:rid AS uuid)"
            ),
            {"rid": rcr["resident_id"]},
        ).mappings().first()

        write_audit_log(
            db,
            request=request,
            actor_user_id=admin["id"],
            actor_email=admin["email"],
            action="resident.update_via_approval",
            entity_type="resident",
            entity_id=rcr["resident_id"],
            before_data=dict(before_resident) if before_resident else None,
            after_data=dict(after_resident) if after_resident else None,
            metadata={"change_request_id": request_id},
        )

    db.execute(
        text(
            """
            UPDATE resident_change_request
            SET status = CAST(:st AS change_request_status),
                reviewed_at = :now,
                reviewed_by_user_id = CAST(:admin_id AS uuid),
                review_note = :note
            WHERE id = CAST(:id AS uuid)
            """
        ),
        {"st": new_status, "now": now, "admin_id": admin["id"], "note": payload.note, "id": request_id},
    )

    db.execute(
        text(
            """
            INSERT INTO change_request_approval (change_request_id, decided_by_user_id, decision, note)
            VALUES (CAST(:crid AS uuid), CAST(:admin_id AS uuid), CAST(:decision AS approval_decision), :note)
            """
        ),
        {"crid": request_id, "admin_id": admin["id"], "decision": "APPROVE" if decision == "approved" else "REJECT", "note": payload.note},
    )

    write_audit_log(
        db,
        request=request,
        actor_user_id=admin["id"],
        actor_email=admin["email"],
        action=f"change_request.{decision}",
        entity_type="resident_change_request",
        entity_id=request_id,
        before_data=dict(rcr),
        after_data={"status": new_status, "review_note": payload.note},
        metadata={"resident_id": rcr["resident_id"]},
    )

    # Return fresh
    updated_rcr = db.execute(
        text(
            """
            SELECT
              id::text AS id,
              resident_id::text AS resident_id,
              requested_by_user_id::text AS requested_by_user_id,
              requested_changes,
              status::text AS status,
              created_at,
              reviewed_at,
              reviewed_by_user_id::text AS reviewed_by_user_id,
              review_note
            FROM resident_change_request
            WHERE id = CAST(:rid AS uuid)
            """
        ),
        {"rid": request_id},
    ).mappings().first()

    return UpdateRequestOut(
        id=updated_rcr["id"],
        residentId=updated_rcr["resident_id"],
        requestedByUserId=updated_rcr.get("requested_by_user_id"),
        fields=updated_rcr["requested_changes"] or {},
        status={"PENDING": "pending", "APPROVED": "approved", "REJECTED": "rejected"}.get(
            updated_rcr["status"], "pending"
        ),
        createdAt=updated_rcr.get("created_at"),
        reviewedAt=updated_rcr.get("reviewed_at"),
        reviewedByUserId=updated_rcr.get("reviewed_by_user_id"),
        reviewNote=updated_rcr.get("review_note"),
    )


@router.get(
    "/audit",
    response_model=List[AuditLogOut],
    summary="List audit log",
    description="Admin-only: list recent audit log entries.",
    operation_id="admin_list_audit_log",
)
def list_audit_log(
    limit: int = Query(50, ge=1, le=500, description="Max entries"),
    admin: Dict = Depends(require_admin),
    db: Session = Depends(get_db),
) -> List[AuditLogOut]:
    """List audit log entries."""
    rows = db.execute(
        text(
            """
            SELECT
              id::text AS id,
              created_at AS at,
              actor_user_id::text AS actor_user_id,
              actor_email::text AS actor_email,
              action,
              entity_type,
              entity_id::text AS entity_id,
              metadata
            FROM audit_log
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"lim": limit},
    ).mappings().all()

    out: List[AuditLogOut] = []
    for r in rows:
        out.append(
            AuditLogOut(
                id=r["id"],
                at=r["at"],
                actorUserId=r.get("actor_user_id"),
                actorEmail=r.get("actor_email"),
                action=r["action"],
                entityType=r["entity_type"],
                entityId=r.get("entity_id"),
                metadata=r.get("metadata"),
            )
        )
    return out
