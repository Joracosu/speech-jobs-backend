"""Health endpoint definitions for service liveness checks."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def get_health() -> dict[str, str]:
    """Return a minimal health payload for basic service checks."""
    return {"status": "ok"}
