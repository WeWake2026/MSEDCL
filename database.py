"""
Unified Database Adapter for MSEDCL Quality Audit System
Supports PostgreSQL (with thread-safe connection pooling, timeouts, and discrete connection params)
and SQLite (with WAL mode, timeouts, and fallback)
"""

import os
import re
import threading
import sqlite3
from urllib.parse import quote_plus
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

def build_database_uri_from_env():
    """
    Construct connection URI from DATABASE_URL or individual parameters:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    """
    direct_url = os.environ.get("DATABASE_URL", "").strip()
    if direct_url:
        return direct_url

    host = os.environ.get("DB_HOST", "").strip()
    name = os.environ.get("DB_NAME", "").strip()
    user = os.environ.get("DB_USER", "").strip()
    password = os.environ.get("DB_PASSWORD", "").strip()
    port = os.environ.get("DB_PORT", "5432").strip()

    if host and name and user:
        pwd_part = f":{quote_plus(password)}" if password else ""
        return f"postgresql://{quote_plus(user)}{pwd_part}@{host}:{port}/{name}"

    return ""

def get_database_uri():
    """Retrieve database connection URI from environment."""
    return build_database_uri_from_env()

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
            timeout_sec = int(os.environ.get("DB_TIMEOUT", "15"))
            _pg_pool = pool.ThreadedConnectionPool(
                min_conn, max_conn, uri,
                connect_timeout=timeout_sec,
                options=f"-c statement_timeout={timeout_sec * 1000}"
            )
    return _pg_pool

def reset_pg_pool():
    """Close and reset active connection pool (used during reconfiguration or tests)."""
    global _pg_pool
    with _pool_lock:
        if _pg_pool is not None:
            try:
                _pg_pool.closeall()
            except Exception:
                pass
            _pg_pool = None

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

    def __iter__(self):
        return iter(self._data)

    def __repr__(self):
        return f"<Row {self._data}>"

class UniversalCursor:
    def __init__(self, cursor, is_pg=False):
        self._cursor = cursor
        self._is_pg = is_pg
        self.lastrowid = getattr(cursor, "lastrowid", None)
        self.description = getattr(cursor, "description", None)

    def _convert_query(self, query):
        if not self._is_pg:
            return query
        # In PostgreSQL:
        # 1. Convert datetime('now') -> NOW()
        converted = re.sub(r"datetime\(\s*'now'\s*\)", "NOW()", query, flags=re.IGNORECASE)
        # 2. Convert standard sqlite '?' placeholders to psycopg2 '%s'
        converted = converted.replace("?", "%s")
        return converted

    def execute(self, query, params=None):
        sql = self._convert_query(query)
        # If PostgreSQL and query is an INSERT into key tables and caller expects lastrowid
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
                    self.description = getattr(self._cursor, "description", None)
                    return self
                except Exception:
                    pass
        
        if params is not None:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        self.lastrowid = getattr(self._cursor, "lastrowid", self.lastrowid)
        self.description = getattr(self._cursor, "description", None)
        return self

    @property
    def rowcount(self):
        return getattr(self._cursor, "rowcount", -1)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

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
        try:
            self._conn.rollback()
        except Exception:
            pass

    def close(self):
        if self._is_pg and self._pool_ref:
            try:
                self._pool_ref.putconn(self._conn)
            except Exception:
                pass
        else:
            try:
                self._conn.close()
            except Exception:
                pass

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
    If PostgreSQL is configured and psycopg2 is available, returns a pooled PostgreSQL UniversalConnection.
    Otherwise, returns an SQLite UniversalConnection.
    """
    uri = get_database_uri()
    if HAS_PSYCOPG2 and uri and (uri.startswith("postgres://") or uri.startswith("postgresql://")):
        try:
            p = _init_pg_pool(uri)
            raw_conn = p.getconn()
            return UniversalConnection(raw_conn, is_pg=True, pool_ref=p)
        except Exception as e:
            # In case pool or connection fails, propagate clean error
            raise ConnectionError(f"PostgreSQL connection failed: {e}")
    else:
        if not db_path:
            db_path = os.environ.get("DATABASE_PATH", "quality_audit.db")
        conn = sqlite3.connect(db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
        except Exception:
            pass
        return UniversalConnection(conn, is_pg=False)

def check_db_health():
    """Returns database health status and details without leaking passwords."""
    is_pg = is_postgres()
    engine_name = "PostgreSQL" if is_pg else "SQLite"
    status = {
        "healthy": False,
        "engine": engine_name,
        "error": None,
        "details": {}
    }
    try:
        with get_db() as conn:
            cur = conn.execute("SELECT 1 AS ok")
            row = cur.fetchone()
            if row and (row["ok"] == 1 or row[0] == 1):
                status["healthy"] = True
                if is_pg:
                    v_row = conn.execute("SELECT version() AS v, current_database() AS db").fetchone()
                    if v_row:
                        status["details"] = {
                            "database": v_row["db"],
                            "version": str(v_row["v"]).split(",")[0]
                        }
                else:
                    v_row = conn.execute("SELECT sqlite_version() AS v").fetchone()
                    status["details"] = {
                        "database": os.environ.get("DATABASE_PATH", "quality_audit.db"),
                        "version": f"SQLite {v_row['v']}" if v_row else "SQLite 3"
                    }
    except Exception as e:
        status["error"] = str(e)
    return status
