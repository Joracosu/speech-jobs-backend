"""Shared FastAPI dependencies for request handling."""

from collections.abc import Generator
from functools import lru_cache

from sqlalchemy.orm import Session, sessionmaker

from app.db.config import create_session_factory


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide cached session factory."""
    return create_session_factory()


def get_db_session() -> Generator[Session, None, None]:
    """Provide a request-scoped database session."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
