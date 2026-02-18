from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.settings import settings
from src.api.routers import admin, auth, favorites, notifications, privacy, resident_workflow, residents

openapi_tags = [
    {"name": "health", "description": "Service health checks."},
    {"name": "auth", "description": "JWT authentication endpoints."},
    {"name": "residents", "description": "Authenticated resident directory endpoints."},
    {"name": "favorites", "description": "User favorites + household view endpoints."},
    {
        "name": "resident",
        "description": "Resident self-service update request workflow + notifications.",
    },
    {"name": "admin", "description": "Admin-only management endpoints."},
]


app = FastAPI(
    title=settings.app_title,
    description=settings.app_description,
    version=settings.app_version,
    openapi_tags=openapi_tags,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=settings.allowed_methods,
    allow_headers=settings.allowed_headers,
)


@app.get(
    "/",
    tags=["health"],
    summary="Health Check",
    description="Basic health check endpoint.",
    operation_id="health_check",
)
def health_check():
    """Return a simple health response."""
    return {"message": "Healthy"}


# Routers
app.include_router(auth.router)
app.include_router(residents.router)
app.include_router(favorites.router)
app.include_router(resident_workflow.router)
app.include_router(notifications.router)
app.include_router(privacy.router)
app.include_router(admin.router)
