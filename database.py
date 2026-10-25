"""
Unified Database Adapter for MSEDCL Quality Audit System
Supports PostgreSQL (with thread-safe connection pooling) and SQLite (with fallback)
"""

import os
import re
import threading
import sqlite3
from contextlib import contextmanager

try:
    import psycopg2
    from psycopg2 import pool
    from psycopg2.extras import RealDictCursor
    from psycopg2 import IntegrityError as PgIntegrityError
    HAS_PSYCOPG2 = True
except ImportError:
    HAS_PSYCOPG2 = False
    PgIntegrityError = sqlite3.IntegrityError

# Unified Integrity Error
DbIntegrityError = (sqlite3.IntegrityError, PgIntegrityError) if HAS_PSYCOPG2 else sqlite3.IntegrityError

_pg_pool = None
_pool_lock = threading.Lock()

def get_database_uri():
    """Retrieve database connection URI from environment."""
    return os.environ.get("DATABASE_URL", "").strip()

def is_postgres():
    """Check if the configured database is PostgreSQL."""
    uri = get_database_uri()
    return bool(uri and (uri.startswith("postgres://") or uri.startswith("postgresql://")))

def _init_pg_pool(uri):
    global _pg_pool
    with _pool_lock:
        if _pg_pool is None:
            # Handle postgres:// vs postgresql://
            if uri.startswith("postgres://"):
                uri = "postgresql://" + uri[len("postgres://"):]
            min_conn = int(os.environ.get("DB_POOL_MIN", "1"))
            max_conn = int(os.environ.get("DB_POOL_MAX", "10"))
            _pg_pool = pool.ThreadedConnectionPool(min_conn, max_conn, uri)
    return _pg_pool

class RowWrapper:
    """Provides key-based and attribute-style access like sqlite3.Row for dict results."""
    def __init__(self, data):
        self._data = dict(data) if data is not None else {}

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data.get(key)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __contains__(self, key):
        return key in self._data

    def __repr__(self):
        return f"<Row {self._data}>"

class UniversalCursor:
    def __init__(self, cursor, is_pg=False):
        self._cursor = cursor
        self._is_pg = is_pg
        self.lastrowid = getattr(cursor, "lastrowid", None)

    def _convert_query(self, query):
        if not self._is_pg:
            return query
        # In PostgreSQL:
        # 1. Convert datetime('now') -> NOW()
        converted = re.sub(r"datetime\(\s*'now'\s*\)", "NOW()", query, flags=re.IGNORECASE)
        # 2. Convert standard sqlite '?' placeholders to psycopg2 '%s'
        # Be careful not to replace ? inside literal strings (naive replacement is usually fine for our queries)
        converted = converted.replace("?", "%s")
        return converted

    def execute(self, query, params=None):
        sql = self._convert_query(query)
        # If PostgreSQL and query is an INSERT into warnings or users and caller expects lastrowid,
        # we can automatically append RETURNING id if not present
        if self._is_pg and re.match(r"^\s*INSERT\s+INTO\s+(warnings|users|audit_logs|email_history|audits)", sql, re.IGNORECASE):
            if "RETURNING" not in sql.upper():
                sql_with_returning = sql.rstrip("; \t\n") + " RETURNING id"
                try:
                    if params:
                        self._cursor.execute(sql_with_returning, params)
                    else:
                        self._cursor.execute(sql_with_returning)
                    row = self._cursor.fetchone()
                    if row:
                        self.lastrowid = row[0] if isinstance(row, (tuple, list)) else row.get("id")
                    return self
                except Exception:
                    # Fallback to standard execution if RETURNING fails (e.g. non-id table)
                    pass
        
        if params is not None:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        self.lastrowid = getattr(self._cursor, "lastrowid", self.lastrowid)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        if self._is_pg:
            return RowWrapper(row)
        return row

    def fetchall(self):
        rows = self._cursor.fetchall()
        if self._is_pg:
            return [RowWrapper(r) for r in rows]
        return rows

    def close(self):
        self._cursor.close()

class UniversalConnection:
    def __init__(self, raw_conn, is_pg=False, pool_ref=None):
        self._conn = raw_conn
        self._is_pg = is_pg
        self._pool_ref = pool_ref

    def cursor(self):
        if self._is_pg:
            cur = self._conn.cursor(cursor_factory=RealDictCursor)
        else:
            cur = self._conn.cursor()
        return UniversalCursor(cur, self._is_pg)

    def execute(self, query, params=None):
        cur = self.cursor()
        cur.execute(query, params)
        return cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        if self._is_pg and self._pool_ref:
            try:
                self._pool_ref.putconn(self._conn)
            except Exception:
                pass
        else:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

def get_db(db_path=None):
    """
    Get a database connection.
    If DATABASE_URL is set and valid, returns a pooled PostgreSQL UniversalConnection.
    Otherwise, returns an SQLite UniversalConnection.
    """
    uri = get_database_uri()
    if HAS_PSYCOPG2 and uri and (uri.startswith("postgres://") or uri.startswith("postgresql://")):
        p = _init_pg_pool(uri)
        raw_conn = p.getconn()
        return UniversalConnection(raw_conn, is_pg=True, pool_ref=p)
    else:
        if not db_path:
            db_path = os.environ.get("DATABASE_PATH", "quality_audit.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return UniversalConnection(conn, is_pg=False)

def check_db_health():
    """Returns database health status and details."""
    is_pg = is_postgres()
    engine_name = "PostgreSQL" if is_pg else "SQLite"
    status = {"healthy": False, "engine": engine_name, "error": None}
    try:
        with get_db() as conn:
            cur = conn.execute("SELECT 1 AS ok")
            row = cur.fetchone()
            if row and (row["ok"] == 1 or row[0] == 1):
                status["healthy"] = True
    except Exception as e:
        status["error"] = str(e)
    return status
