"""SQLite engine and session helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        db_path = get_settings().db_path
        # Ensure the parent directory exists (e.g. ./data or /data).
        parent = os.path.dirname(os.path.abspath(db_path))
        os.makedirs(parent, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def init_db() -> None:
    # Import models so they register on SQLModel.metadata before create_all.
    from . import models  # noqa: F401

    engine = get_engine()
    SQLModel.metadata.create_all(engine)
    _migrate(engine)


def _migrate(engine) -> None:
    """Tiny additive migrations for columns added to pre-existing tables.

    create_all() creates missing tables but never alters existing ones, so a DB
    from an earlier version needs new columns added by hand.
    """
    with engine.begin() as conn:
        book_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(book)")}
        if book_cols and "info_fetched" not in book_cols:
            conn.exec_driver_sql("ALTER TABLE book ADD COLUMN info_fetched INTEGER NOT NULL DEFAULT 0")
        if book_cols and "chapters_update_time" not in book_cols:
            conn.exec_driver_sql("ALTER TABLE book ADD COLUMN chapters_update_time INTEGER NOT NULL DEFAULT 0")
        # Columns dropped from the model (public/dead data). Remove them so ORM
        # inserts that omit them don't hit a NOT NULL constraint. bookmark_count was
        # always 0 (the API's bookmarkCount is unused); 划线 lives in note_count.
        for col in ("rating", "rating_count", "bookmark_count"):
            if col in book_cols:
                conn.exec_driver_sql(f"ALTER TABLE book DROP COLUMN {col}")


@contextmanager
def session_scope() -> Iterator[Session]:
    # expire_on_commit=False keeps already-loaded column values readable on
    # instances after the session closes (templates render post-scope).
    session = Session(get_engine(), expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
