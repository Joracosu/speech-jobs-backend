"""FastAPI application bootstrap for speech-jobs-backend."""

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.core.settings import get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_title,
        debug=settings.app_debug,
        version=settings.app_version,
    )
    application.include_router(health_router)
    return application


app = create_app()
