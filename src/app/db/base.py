"""Shared SQLAlchemy ORM foundations for speech-jobs-backend."""

from datetime import UTC, datetime

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """Return the current UTC-aware timestamp for ORM defaults."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Base declarative class shared by the project's ORM models."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
