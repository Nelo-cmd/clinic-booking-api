import os
import re
import sqlite3
import psycopg2
from psycopg2 import pool
from contextlib import contextmanager
from datetime import datetime, timezone

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# Neon connection string fix if user pasted postgres:// instead of postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

pg_pool = None

def is_postgres():
    return bool(DATABASE_URL and (DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")))

def init_db():
    global pg_pool
    if is_postgres():
        if pg_pool is None:
            pg_pool = pool.SimpleConnectionPool(minconn=1, maxconn=20, dsn=DATABASE_URL)
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                CREATE TABLE IF NOT EXISTS slots (
                    id TEXT PRIMARY KEY,
                    provider TEXT NOT NULL,
                    start_time TIMESTAMPTZ NOT NULL,
                    end_time TIMESTAMPTZ NOT NULL
                );

                CREATE TABLE IF NOT EXISTS bookings (
                    id TEXT PRIMARY KEY,
                    slot_id TEXT NOT NULL REFERENCES slots(id) ON DELETE CASCADE,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'confirmed',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_one_confirmed_slot
                    ON bookings (slot_id) WHERE status = 'confirmed';

                CREATE INDEX IF NOT EXISTS idx_bookings_email_created
                    ON bookings (email, created_at);

                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    key TEXT PRIMARY KEY,
                    body_hash TEXT NOT NULL,
                    status_code INT NOT NULL,
                    response_body TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE TABLE IF NOT EXISTS rate_limits (
                    id SERIAL PRIMARY KEY,
                    email TEXT NOT NULL,
                    attempted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );

                CREATE INDEX IF NOT EXISTS idx_rate_limits_email_attempt
                    ON rate_limits (email, attempted_at);
                """)
            conn.commit()
    else:
        # SQLite fallback for local testing
        db_path = os.environ.get("SQLITE_PATH", "clinic_local.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS slots (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bookings (
            id TEXT PRIMARY KEY,
            slot_id TEXT NOT NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'confirmed',
            created_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_one_confirmed_slot
            ON bookings (slot_id) WHERE status = 'confirmed';

        CREATE INDEX IF NOT EXISTS idx_bookings_email_created
            ON bookings (email, created_at);

        CREATE TABLE IF NOT EXISTS idempotency_keys (
            key TEXT PRIMARY KEY,
            body_hash TEXT NOT NULL,
            status_code INT NOT NULL,
            response_body TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS rate_limits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_rate_limits_email_attempt
            ON rate_limits (email, attempted_at);
        """)
        conn.commit()
        conn.close()

@contextmanager
def get_db_connection():
    if is_postgres():
        global pg_pool
        if pg_pool is None:
            init_db()
        conn = pg_pool.getconn()
        try:
            yield conn
        finally:
            pg_pool.putconn(conn)
    else:
        db_path = os.environ.get("SQLITE_PATH", "clinic_local.db")
        conn = sqlite3.connect(db_path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

def format_iso(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        # ensure Z ending if UTC
        if dt.endswith("+00:00"):
            return dt[:-6] + "Z"
        return dt
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(dt)

def parse_iso(dt_str):
    if not dt_str:
        return None
    cleaned = dt_str.replace("Z", "+00:00")
    return datetime.fromisoformat(cleaned)
