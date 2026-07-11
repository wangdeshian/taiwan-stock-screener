from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from taiwan_stock_screener.config import get_settings


class Base(DeclarativeBase):
    pass


def _ensure_sqlite_parent(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return
    db_path = database_url.replace("sqlite:///", "", 1)
    if db_path == ":memory:":
        return
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)


def create_session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    url = database_url or get_settings().database_url
    _ensure_sqlite_parent(url)
    engine = create_engine(url, future=True)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


SessionLocal = create_session_factory()


def init_db() -> None:
    from taiwan_stock_screener.database import models  # noqa: F401

    Base.metadata.create_all(bind=SessionLocal.kw["bind"])


def get_db() -> Generator[Session, None, None]:
    init_db()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
