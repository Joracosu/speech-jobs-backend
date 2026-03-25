"""FastAPI application bootstrap for speech-jobs-backend."""

from fastapi import FastAPI

from app.api.routes.health import router as health_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(title="speech-jobs-backend")
    application.include_router(health_router)
    return application


app = create_app()
