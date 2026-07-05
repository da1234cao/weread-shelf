"""SQLite engine and session helpers."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
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
        # timeout = SQLite busy-wait: a reader blocks briefly instead of failing
        # outright when a pull holds the write lock.
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        # WAL lets the dashboard keep reading while a pull writes — without it a
        # long pull transaction can surface as "database is locked" 500s.
        @event.listens_for(_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

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
        settings_cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(app_settings)")}
        if settings_cols and "retention_days" not in settings_cols:
            conn.exec_driver_sql("ALTER TABLE app_settings ADD COLUMN retention_days INTEGER NOT NULL DEFAULT 0")
        if settings_cols and "gateway_interval" not in settings_cols:
            conn.exec_driver_sql("ALTER TABLE app_settings ADD COLUMN gateway_interval FLOAT NOT NULL DEFAULT 0.2")
        if settings_cols and "stats_backfilled" not in settings_cols:
            conn.exec_driver_sql("ALTER TABLE app_settings ADD COLUMN stats_backfilled BOOLEAN NOT NULL DEFAULT 0")
        if settings_cols and "show_footer_credit" not in settings_cols:
            conn.exec_driver_sql("ALTER TABLE app_settings ADD COLUMN show_footer_credit BOOLEAN NOT NULL DEFAULT 1")
        if settings_cols and "health_monitor_enabled" not in settings_cols:
            conn.exec_driver_sql("ALTER TABLE app_settings ADD COLUMN health_monitor_enabled BOOLEAN NOT NULL DEFAULT 1")

        # Migrate shelf_item / recommendation from composite-PK snapshots to
        # single-PK upsert tables (book_id only). Keeps the latest row per book.
        _migrate_dedup_pk(conn, "shelf_item", "book_id", "pull_date")
        _migrate_dedup_pk(conn, "recommendation", "book_id", "pull_date")


def _migrate_dedup_pk(conn, table: str, new_pk: str, date_col: str) -> None:
    """Rebuild *table* so *new_pk* is its sole primary key.

    Detects the old composite-PK schema by checking whether *date_col* is still
    part of the PK. When it is, keeps only the latest row per *new_pk*.
    """
    pk_cols = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})") if row[5]}
    if date_col not in pk_cols:
        return  # already migrated or never had the old schema

    # Sniff all column definitions from the live table so the new one matches
    # the current model exactly (including any columns added later).
    col_defs = []
    for row in conn.exec_driver_sql(f"PRAGMA table_info({table})"):
        name, ctype, notnull, dflt = row[1], row[2], row[3], row[4]
        constraints = []
        if name == new_pk:
            constraints.append("PRIMARY KEY")
        if notnull and name != new_pk:  # PK implies NOT NULL
            constraints.append("NOT NULL")
        if dflt is not None:
            constraints.append(f"DEFAULT {dflt}")
        col_defs.append(f"{name} {ctype} {' '.join(constraints)}".strip())

    tmp = f"{table}_migrate_tmp"
    conn.exec_driver_sql(f"CREATE TABLE {tmp} ({', '.join(col_defs)})")

    # Keep only the latest row per new_pk (correlated subquery, works for any row count).
    conn.exec_driver_sql(
        f"INSERT INTO {tmp} SELECT * FROM {table} AS t1"
        f"  WHERE t1.{new_pk} IS NOT NULL AND t1.{new_pk} != ''"
        f"    AND t1.{date_col} = (SELECT MAX(t2.{date_col}) FROM {table} AS t2 WHERE t2.{new_pk} = t1.{new_pk})"
    )

    conn.exec_driver_sql(f"DROP TABLE {table}")
    conn.exec_driver_sql(f"ALTER TABLE {tmp} RENAME TO {table}")


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
