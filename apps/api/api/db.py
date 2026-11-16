"""Database engine, session factory and schema bootstrap."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from api.config import settings


def _sqlalchemy_url(value: str) -> str:
    """Use the installed psycopg 3 driver for Postgres URLs.

    Neon and other hosted providers hand out ``postgresql://`` URLs, which SQLAlchemy
    otherwise resolves to psycopg2. The project ships psycopg 3, so make the driver
    explicit while leaving already-qualified URLs and SQLite untouched.
    """
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://") :]
    if value.startswith("postgresql://"):
        return "postgresql+psycopg://" + value[len("postgresql://") :]
    return value


_database_url = _sqlalchemy_url(settings.database_url)
_connect_args: dict[str, Any] = {}
if _database_url.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine: Engine = create_engine(
    _database_url,
    pool_pre_ping=True,
    future=True,
    connect_args=_connect_args,
)

if _database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:  # pragma: no cover
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


class Base(DeclarativeBase):
    pass


def init_db(target_engine: Engine | None = None) -> None:
    """Create tables in dev/test. Production schema is owned by Alembic."""
    import api.models  # noqa: F401  (register metadata)

    if not settings.auto_create_schema:
        return
    Base.metadata.create_all(bind=target_engine or engine)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
