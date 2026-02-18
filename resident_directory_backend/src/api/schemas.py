from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, EmailStr, Field


class TokenResponse(BaseModel):
    token: str = Field(..., description="JWT access token")
    user: "AuthUser" = Field(..., description="Authenticated user payload")


class AuthUser(BaseModel):
    id: str = Field(..., description="User UUID")
    email: EmailStr = Field(..., description="User email")
    role: Literal["admin", "resident"] = Field(..., description="User role")
    residentId: Optional[str] = Field(None, description="Linked resident UUID (if role=resident)")


class LoginRequest(BaseModel):
    email: EmailStr = Field(..., description="User email")
    password: str = Field(..., min_length=1, description="User password")


class ResidentOut(BaseModel):
    id: str = Field(..., description="Resident UUID")
    name: str = Field(..., description="Full name: 'First Last'")
    unit: str = Field(..., description="Unit identifier (e.g., '1A')")
    phone: Optional[str] = Field(None, description="Optional phone number")
    email: Optional[EmailStr] = Field(None, description="Optional email address")
    createdAt: Optional[datetime] = Field(None, description="Created timestamp")
    updatedAt: Optional[datetime] = Field(None, description="Updated timestamp")


class ResidentCreate(BaseModel):
    name: str = Field(..., description="Full name: 'First Last'")
    unit: str = Field(..., description="Unit identifier")
    phone: Optional[str] = Field(None, description="Optional phone")
    email: Optional[EmailStr] = Field(None, description="Optional email")


class ResidentPatch(BaseModel):
    name: Optional[str] = Field(None, description="Full name: 'First Last'")
    unit: Optional[str] = Field(None, description="Unit identifier")
    phone: Optional[str] = Field(None, description="Optional phone")
    email: Optional[EmailStr] = Field(None, description="Optional email")
    isActive: Optional[bool] = Field(None, description="Active flag")


class UpdateRequestCreate(BaseModel):
    residentId: str = Field(..., description="Resident UUID to update")
    fields: Dict[str, Any] = Field(
        ...,
        description="Requested changes. Only phone/email allowed.",
        examples=[{"phone": "555-1234"}],
    )


class UpdateRequestOut(BaseModel):
    id: str = Field(..., description="Change request UUID")
    residentId: str = Field(..., description="Resident UUID")
    requestedByUserId: Optional[str] = Field(None, description="Requesting user UUID")
    fields: Dict[str, Any] = Field(..., description="Requested fields (phone/email)")
    status: Literal["pending", "approved", "rejected"] = Field(..., description="Workflow status")
    createdAt: Optional[datetime] = Field(None, description="Created timestamp")
    reviewedAt: Optional[datetime] = Field(None, description="Reviewed timestamp")
    reviewedByUserId: Optional[str] = Field(None, description="Reviewer user UUID")
    reviewNote: Optional[str] = Field(None, description="Review note")


class ReviewDecisionIn(BaseModel):
    decision: Literal["approved", "rejected"] = Field(..., description="Admin decision")
    note: Optional[str] = Field(None, description="Optional review note")


class CsvImportError(BaseModel):
    row: int = Field(..., description="1-based CSV row number (excluding header)")
    message: str = Field(..., description="Error message for this row")


class CsvImportResult(BaseModel):
    imported: int = Field(..., description="Number of inserted residents")
    updated: int = Field(..., description="Number of updated residents")
    errors: List[CsvImportError] = Field(default_factory=list, description="Row-level errors")


class AuditLogOut(BaseModel):
    id: str = Field(..., description="Audit log UUID")
    at: datetime = Field(..., description="Event time")
    actorUserId: Optional[str] = Field(None, description="Actor user UUID")
    actorEmail: Optional[str] = Field(None, description="Actor email")
    action: str = Field(..., description="Action string")
    entityType: str = Field(..., description="Entity type")
    entityId: Optional[str] = Field(None, description="Entity UUID")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Free-form metadata")
"""
Forward references
"""
TokenResponse.model_rebuild()
