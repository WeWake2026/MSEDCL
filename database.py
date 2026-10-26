"""
Unified Database Adapter for MSEDCL Quality Audit System
Supports PostgreSQL (with thread-safe connection pooling, timeouts, and discrete connection params)
and SQLite (with WAL mode, timeouts, and fallback)
"""

import os
import sys
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

_env_loaded = False
def _load_env_if_present():
    """Ensure .env is read into os.environ if present (runs once initially)."""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception:
            pass

_load_env_if_present()

def build_database_uri_from_env():
    """
    Construct connection URI from DATABASE_URL or individual parameters:
    DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    """
    _load_env_if_present()
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

_pg_pool = None
_pool_pid = None
_pool_lock = threading.Lock()

def _init_pg_pool(uri):
    global _pg_pool, _pool_pid
    current_pid = os.getpid()
    with _pool_lock:
        # If running under gunicorn with multiple worker processes (pre-fork),
        # each child process MUST have its own independent connection pool.
        # Sharing a parent process socket across forks causes SSL decryption failed / bad record MAC.
        if _pg_pool is None or _pool_pid != current_pid:
            # Handle postgres:// vs postgresql://
            if uri.startswith("postgres://"):
                uri = "postgresql://" + uri[len("postgres://"):]
            
            # Ensure sslmode=require for external cloud connections that need TLS
            is_external_cloud = any(h in uri for h in ("render.com", "aws", "supabase", "neon"))
            if "sslmode=" not in uri and is_external_cloud:
                delimiter = "&" if "?" in uri else "?"
                uri = f"{uri}{delimiter}sslmode=require"

            min_conn = int(os.environ.get("DB_POOL_MIN", "1"))
            max_conn = int(os.environ.get("DB_POOL_MAX", "10"))
            timeout_sec = int(os.environ.get("DB_TIMEOUT", "15"))
            _pg_pool = pool.ThreadedConnectionPool(
                min_conn, max_conn, uri,
                connect_timeout=timeout_sec,
                options=f"-c statement_timeout={timeout_sec * 1000}"
            )
            _pool_pid = current_pid
    return _pg_pool

def reset_pg_pool():
    """Close and reset active connection pool (used during reconfiguration or tests)."""
    global _pg_pool, _pool_pid
    with _pool_lock:
        if _pg_pool is not None:
            try:
                _pg_pool.closeall()
            except Exception:
                pass
            _pg_pool = None
            _pool_pid = None

class RowWrapper:
    """Provides key-based and attribute-style access like sqlite3.Row for dict results."""
    def __init__(self, data):
        self._data = dict(data) if data is not None else {}

    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self._data.values())[key]
        return self._data.get(key)

    def __setitem__(self, key, value):
        self._data[key] = value

    def __getattr__(self, name):
        if name in self._data:
            return self._data[name]
        raise AttributeError(f"'RowWrapper' object has no attribute '{name}'")

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

    def _convert_query(self, query, has_params=False):
        if not self._is_pg:
            return query
        # In PostgreSQL:
        # 1. Convert datetime('now') -> NOW()
        converted = re.sub(r"datetime\(\s*'now'\s*\)", "NOW()", query, flags=re.IGNORECASE)
        # 2. Convert standard sqlite '?' placeholders to psycopg2 '%s'
        converted = converted.replace("?", "%s")
        # 3. If params are passed, psycopg2 will perform pyformat interpolation.
        #    Literal percent signs (e.g. LIKE '%PIP%') must be escaped to '%%',
        #    WITHOUT doubling existing '%s' placeholders.
        if has_params:
            converted = re.sub(r"%(?!s)", "%%", converted)

        # 4. Convert SQLite 'INSERT OR IGNORE INTO table ...' to 'INSERT INTO table ... ON CONFLICT DO NOTHING'
        if re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", converted, re.IGNORECASE):
            converted = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", converted, flags=re.IGNORECASE)
            if "ON CONFLICT" not in converted.upper():
                converted = converted.rstrip("; \t\n") + " ON CONFLICT DO NOTHING"
        return converted

    def execute(self, query, params=None):
        # Normalize empty list/tuple params to None if no parameters were supplied
        if params is not None and len(params) == 0:
            params = None
        has_params = params is not None and len(params) > 0
        sql = self._convert_query(query, has_params=has_params)

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
        self._is_broken = False

    @property
    def is_pg(self):
        return self._is_pg

    def cursor(self):
        if self._is_pg:
            cur = self._conn.cursor(cursor_factory=RealDictCursor)
        else:
            cur = self._conn.cursor()
        return UniversalCursor(cur, self._is_pg)

    def execute(self, query, params=None):
        try:
            cur = self.cursor()
            cur.execute(query, params)
            return cur
        except Exception as e:
            # Detect connection loss or SSL decryption/bad record mac errors
            if self._is_pg:
                err_str = str(e).lower()
                if "ssl" in err_str or "closed" in err_str or "terminated" in err_str or "connection" in err_str:
                    self._is_broken = True
            raise

    def commit(self):
        try:
            self._conn.commit()
        except Exception as e:
            if self._is_pg:
                self._is_broken = True
            raise

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            if self._is_pg:
                self._is_broken = True

    def close(self):
        if self._is_pg and self._pool_ref:
            try:
                # If connection suffered an SSL failure or socket error, discard it completely from the pool
                is_closed = getattr(self._conn, "closed", 0) != 0
                if self._is_broken or is_closed:
                    self._pool_ref.putconn(self._conn, close=True)
                else:
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

def _check_connection_liveness(raw_conn):
    """Ensure raw PostgreSQL connection socket and SSL stream are healthy."""
    if getattr(raw_conn, "closed", 0) != 0:
        return False
    try:
        # Check transaction status or run a lightweight test
        with raw_conn.cursor() as cur:
            cur.execute("SELECT 1")
        raw_conn.commit()
        return True
    except Exception:
        return False

def get_db(db_path=None):
    """
    Get a database connection.
    If PostgreSQL is configured and psycopg2 is available, returns a pooled PostgreSQL UniversalConnection.
    Otherwise, returns an SQLite UniversalConnection.
    """
    uri = get_database_uri()
    is_prod = os.environ.get("APP_ENV", "").lower() in ("production", "prod")
    if HAS_PSYCOPG2 and uri and (uri.startswith("postgres://") or uri.startswith("postgresql://")):
        try:
            p = _init_pg_pool(uri)
            # Try getting a healthy connection, discarding stale/corrupt SSL sockets up to 3 times
            raw_conn = None
            for _ in range(3):
                candidate = p.getconn()
                if _check_connection_liveness(candidate):
                    raw_conn = candidate
                    break
                else:
                    # Stale or corrupted SSL stream - close and discard from pool
                    try:
                        p.putconn(candidate, close=True)
                    except Exception:
                        pass
            
            if raw_conn is None:
                # If existing pool connections were stale, reset pool and get a fresh connection
                reset_pg_pool()
                p = _init_pg_pool(uri)
                raw_conn = p.getconn()

            return UniversalConnection(raw_conn, is_pg=True, pool_ref=p)
        except Exception as e:
            # In production, never silently fall back to an ephemeral SQLite database
            if is_prod:
                reset_pg_pool()
                raise RuntimeError(f"CRITICAL: Production PostgreSQL connection failed: {e}") from e

            # In development/test environments, log warning and fall back to SQLite to aid offline dev
            print(f"[WARNING] PostgreSQL connection failed ({e}). Falling back to local SQLite engine.", file=sys.stderr)
            reset_pg_pool()
    elif is_prod:
        raise RuntimeError("CRITICAL: Production requires a configured PostgreSQL database (DATABASE_URL or DB_*).")

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
            actual_is_pg = getattr(conn, "is_pg", False)
            engine_name = "PostgreSQL" if actual_is_pg else "SQLite"
            status["engine"] = engine_name
            cur = conn.execute("SELECT 1 AS ok")
            row = cur.fetchone()
            if row and (row["ok"] == 1 or row[0] == 1):
                status["healthy"] = True
                if actual_is_pg:
                    v_row = conn.execute("SELECT version() AS v, current_database() AS db, current_user AS u, inet_server_port() AS port").fetchone()
                    if v_row:
                        status["details"] = {
                            "database": v_row.get("db"),
                            "current_user": v_row.get("u"),
                            "server_port": v_row.get("port"),
                            "version": str(v_row.get("v")).split(",")[0]
                        }
                    # Count tables in public schema
                    t_rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename").fetchall()
                    status["details"]["tables"] = [r.get("tablename") for r in t_rows]
                else:
                    v_row = conn.execute("SELECT sqlite_version() AS v").fetchone()
                    status["details"] = {
                        "database": os.environ.get("DATABASE_PATH", "quality_audit.db"),
                        "version": f"SQLite {v_row['v']}" if v_row else "SQLite 3"
                    }
                    t_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                    status["details"]["tables"] = [r["name"] if hasattr(r, "__getitem__") else r[0] for r in t_rows]
    except Exception as e:
        status["error"] = "Database connection error (details logged securely)"
    return status
