"""Lazy database bootstrap helpers for speech-jobs-backend."""

from sqlalchemy import Engine
from sqlalchemy import create_engine as sqlalchemy_create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.settings import get_settings


def get_database_url() -> str:
    """Return the configured database URL without opening a connection."""
    return get_settings().database_url


def create_engine() -> Engine:
    """Create a SQLAlchemy engine without validating live connectivity."""
    return sqlalchemy_create_engine(
        get_database_url(),
        future=True,
        pool_pre_ping=True,
    )


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Create a session factory bound to the configured engine."""
    database_engine = engine or create_engine()
    return sessionmaker(
        bind=database_engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
