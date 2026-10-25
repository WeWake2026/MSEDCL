"""
Database Explorer Service for MSEDCL Quality Audit System
Supports PostgreSQL and SQLite metadata introspection, paginated record viewing,
column schema analysis, safe query execution, and CSV generation with formula injection protection.
"""

import re
import csv
import io
from database import get_db, is_postgres, HAS_PSYCOPG2

# Sensitive column names that must be redacted/masked in views and exports
SENSITIVE_COLUMNS = {
    "password", "password_hash", "token", "secret", "access_token",
    "refresh_token", "api_key", "private_key", "auth_token"
}

# Tables restricted from viewing or exporting by non-super users
BLOCKED_TABLES = {"pg_shadow", "pg_authid", "sqlite_stat1"}

def sanitize_csv_cell(value):
    """
    Prevents CSV Formula Injection (CWE-1236).
    If a cell starts with =, +, -, @, \t, or \r, prefix with a single quote.
    """
    if value is None:
        return ""
    val_str = str(value)
    if val_str and val_str[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val_str}"
    return val_str

def get_db_overview():
    """
    Returns connection status, server version, environment, available schemas,
    and table counts (approximate and exact counts distinguished).
    """
    is_pg = is_postgres()
    overview = {
        "engine": "PostgreSQL" if is_pg else "SQLite",
        "connected": False,
        "database_name": "",
        "server_version": "Unknown",
        "schemas": [],
        "total_tables": 0,
        "total_views": 0,
        "tables_summary": [],
        "error": None
    }

    try:
        with get_db() as conn:
            cur = conn.execute("SELECT 1 AS ok")
            cur.fetchone()
            overview["connected"] = True
            is_pg = getattr(conn, "is_pg", False)
            overview["engine"] = "PostgreSQL" if is_pg else "SQLite"

            if is_pg:
                # PostgreSQL metadata
                v_row = conn.execute("SELECT current_database() AS db, version() AS ver, current_user AS u").fetchone()
                overview["database_name"] = v_row["db"] if v_row else "postgres"
                overview["server_version"] = str(v_row["ver"]).split(",")[0] if v_row else "PostgreSQL"
                overview["current_user"] = v_row["u"] if v_row else "unknown"

                # Schemas
                schemas_cur = conn.execute("""
                    SELECT schema_name 
                    FROM information_schema.schemata 
                    WHERE schema_name NOT IN ('pg_toast', 'pg_temp_1', 'pg_toast_temp_1')
                    ORDER BY schema_name
                """)
                overview["schemas"] = [r["schema_name"] for r in schemas_cur.fetchall()]

                # Tables & Views
                tbls_cur = conn.execute("""
                    SELECT 
                        table_schema,
                        table_name,
                        table_type
                    FROM information_schema.tables
                    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema, table_name
                """)
                all_tbls = tbls_cur.fetchall()
                overview["total_tables"] = sum(1 for t in all_tbls if t["table_type"] == "BASE TABLE")
                overview["total_views"] = sum(1 for t in all_tbls if t["table_type"] == "VIEW")

                # Get row counts using estimates from pg_class where possible
                summary = []
                for t in all_tbls:
                    s_name = t["table_schema"]
                    t_name = t["table_name"]
                    # Approximate count from pg_class reltuples
                    est_cur = conn.execute("""
                        SELECT c.reltuples::bigint AS est_rows
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = %s AND c.relname = %s
                    """, (s_name, t_name))
                    est_row = est_cur.fetchone()
                    est_cnt = est_row["est_rows"] if est_row and est_row["est_rows"] is not None else -1
                    if est_cnt < 0 or est_cnt < 5000:
                        # For smaller or new tables, get exact count safely
                        try:
                            # Table and schema validated from information_schema
                            cnt_cur = conn.execute(f'SELECT COUNT(1) AS cnt FROM "{s_name}"."{t_name}"')
                            row_cnt = cnt_cur.fetchone()["cnt"]
                            is_exact = True
                        except Exception:
                            row_cnt = est_cnt if est_cnt >= 0 else 0
                            is_exact = False
                    else:
                        row_cnt = est_cnt
                        is_exact = False

                    summary.append({
                        "schema": s_name,
                        "name": t_name,
                        "type": "Table" if t["table_type"] == "BASE TABLE" else "View",
                        "row_count": row_cnt,
                        "is_exact": is_exact
                    })
                overview["tables_summary"] = summary

            else:
                # SQLite metadata
                v_row = conn.execute("SELECT sqlite_version() AS ver").fetchone()
                overview["database_name"] = "quality_audit.db"
                overview["server_version"] = f"SQLite {v_row['ver']}" if v_row else "SQLite 3"
                overview["schemas"] = ["main"]

                cur = conn.execute("""
                    SELECT name, type FROM sqlite_master 
                    WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'
                    ORDER BY type, name
                """)
                tbls = cur.fetchall()
                overview["total_tables"] = sum(1 for t in tbls if t["type"] == "table")
                overview["total_views"] = sum(1 for t in tbls if t["type"] == "view")

                summary = []
                for t in tbls:
                    t_name = t["name"]
                    try:
                        cnt = conn.execute(f'SELECT COUNT(1) AS cnt FROM "{t_name}"').fetchone()["cnt"]
                    except Exception:
                        cnt = 0
                    summary.append({
                        "schema": "main",
                        "name": t_name,
                        "type": "Table" if t["type"] == "table" else "View",
                        "row_count": cnt,
                        "is_exact": True
                    })
                overview["tables_summary"] = summary

    except Exception as e:
        overview["error"] = str(e)

    return overview

def get_table_schema(table_name, schema_name="public"):
    """
    Returns column details: name, type, nullable, primary key, default.
    Validates table_name strictly to prevent SQL injection.
    """
    is_pg = is_postgres()
    columns = []
    indexes = []

    # Strict identifier check
    if not re.match(r"^[A-Za-z0-9_]+$", table_name):
        raise ValueError("Invalid table name")
    if not re.match(r"^[A-Za-z0-9_]+$", schema_name):
        raise ValueError("Invalid schema name")

    with get_db() as conn:
        is_pg = getattr(conn, "is_pg", False)
        if is_pg:
            col_cur = conn.execute("""
                SELECT 
                    c.column_name,
                    c.data_type,
                    c.is_nullable,
                    c.column_default,
                    CASE WHEN pk.column_name IS NOT NULL THEN 1 ELSE 0 END AS is_pk
                FROM information_schema.columns c
                LEFT JOIN (
                    SELECT kcu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                      AND tc.table_schema = kcu.table_schema
                    WHERE tc.constraint_type = 'PRIMARY KEY'
                      AND tc.table_schema = %s
                      AND tc.table_name = %s
                ) pk ON pk.column_name = c.column_name
                WHERE c.table_schema = %s AND c.table_name = %s
                ORDER BY c.ordinal_position
            """, (schema_name, table_name, schema_name, table_name))
            for r in col_cur.fetchall():
                columns.append({
                    "name": r["column_name"],
                    "type": r["data_type"],
                    "nullable": r["is_nullable"] == "YES",
                    "default": r["column_default"],
                    "is_pk": bool(r["is_pk"]),
                    "is_sensitive": r["column_name"].lower() in SENSITIVE_COLUMNS
                })

            # Indexes
            idx_cur = conn.execute("""
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = %s AND tablename = %s
            """, (schema_name, table_name))
            for r in idx_cur.fetchall():
                indexes.append({"name": r["indexname"], "def": r["indexdef"]})

        else:
            # SQLite PRAGMA table_info
            pragma_cur = conn.execute(f'PRAGMA table_info("{table_name}")')
            for r in pragma_cur.fetchall():
                columns.append({
                    "name": r["name"],
                    "type": r["type"],
                    "nullable": not bool(r["notnull"]),
                    "default": r["dflt_value"],
                    "is_pk": bool(r["pk"]),
                    "is_sensitive": r["name"].lower() in SENSITIVE_COLUMNS
                })

            idx_cur = conn.execute(f'PRAGMA index_list("{table_name}")')
            for r in idx_cur.fetchall():
                indexes.append({"name": r["name"], "unique": bool(r["unique"])})

    return {"table": table_name, "schema": schema_name, "columns": columns, "indexes": indexes}

def get_table_records(table_name, schema_name="public", page=1, per_page=25, search="", sort_col=None, sort_dir="ASC", filter_col=None, filter_val=""):
    """
    Fetches paginated records with safe filtering, column-specific filters, and sorting.
    Masks sensitive columns.
    """
    if table_name.lower() in BLOCKED_TABLES:
        raise PermissionError(f"Access to table '{table_name}' is restricted.")

    schema_info = get_table_schema(table_name, schema_name)
    columns = [col["name"] for col in schema_info["columns"]]
    if not columns:
        raise ValueError(f"Table '{table_name}' not found.")

    is_pg = is_postgres()
    page = max(1, int(page))
    per_page = min(max(10, int(per_page)), 200)
    offset = (page - 1) * per_page

    # Validate sorting column strictly against existing columns
    if sort_col and sort_col not in columns:
        sort_col = None
    if not sort_col:
        # Default to first primary key or first column
        pk_cols = [c["name"] for c in schema_info["columns"] if c["is_pk"]]
        sort_col = pk_cols[0] if pk_cols else columns[0]

    sort_dir = "DESC" if str(sort_dir).upper() == "DESC" else "ASC"

    with get_db() as conn:
        is_pg = getattr(conn, "is_pg", False)

        # Build search clause parameterized
        where_clauses = []
        params = []
        if search:
            search_terms = []
            for col_name in columns:
                if is_pg:
                    search_terms.append(f'CAST("{col_name}" AS TEXT) ILIKE %s')
                    params.append(f"%{search}%")
                else:
                    search_terms.append(f'CAST("{col_name}" AS TEXT) LIKE ?')
                    params.append(f"%{search}%")
            where_clauses.append("(" + " OR ".join(search_terms) + ")")

        # Column-specific targeted filter
        if filter_col and filter_col in columns and filter_val:
            if is_pg:
                where_clauses.append(f'CAST("{filter_col}" AS TEXT) ILIKE %s')
                params.append(f"%{filter_val}%")
            else:
                where_clauses.append(f'CAST("{filter_col}" AS TEXT) LIKE ?')
                params.append(f"%{filter_val}%")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        tbl_qualified = f'"{schema_name}"."{table_name}"' if is_pg else f'"{table_name}"'

        # Total count for pagination
        count_sql = f'SELECT COUNT(1) AS total FROM {tbl_qualified} {where_sql}'
        count_res = conn.execute(count_sql, tuple(params)).fetchone()
        total_rows = count_res["total"] if count_res else 0

        # Paginated records
        records_sql = f'SELECT * FROM {tbl_qualified} {where_sql} ORDER BY "{sort_col}" {sort_dir} LIMIT {per_page} OFFSET {offset}'
        cur = conn.execute(records_sql, tuple(params))
        raw_rows = cur.fetchall()

        masked_rows = []
        for r in raw_rows:
            row_dict = {}
            for col in columns:
                val = r[col]
                if col.lower() in SENSITIVE_COLUMNS and val is not None:
                    row_dict[col] = "•••••••• [REDACTED]"
                else:
                    row_dict[col] = val
            masked_rows.append(row_dict)

    total_pages = max(1, (total_rows + per_page - 1) // per_page)
    return {
        "table": table_name,
        "schema": schema_name,
        "columns": schema_info["columns"],
        "records": masked_rows,
        "total_rows": total_rows,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "sort_col": sort_col,
        "sort_dir": sort_dir,
        "search": search,
        "filter_col": filter_col,
        "filter_val": filter_val
    }

def export_table_csv(table_name, schema_name="public", search="", filter_col=None, filter_val="", max_limit=10000):
    """
    Streams CSV data for the requested table.
    Enforces row limits, masks sensitive fields, and prevents CSV formula injection.
    """
    if table_name.lower() in BLOCKED_TABLES:
        raise PermissionError(f"Access to table '{table_name}' is restricted.")

    schema_info = get_table_schema(table_name, schema_name)
    columns = [col["name"] for col in schema_info["columns"]]
    if not columns:
        raise ValueError(f"Table '{table_name}' not found.")

    with get_db() as conn:
        is_pg = getattr(conn, "is_pg", False)
        where_clauses = []
        params = []
        if search:
            search_terms = []
            for col_name in columns:
                if is_pg:
                    search_terms.append(f'CAST("{col_name}" AS TEXT) ILIKE %s')
                    params.append(f"%{search}%")
                else:
                    search_terms.append(f'CAST("{col_name}" AS TEXT) LIKE ?')
                    params.append(f"%{search}%")
            where_clauses.append("(" + " OR ".join(search_terms) + ")")

        if filter_col and filter_col in columns and filter_val:
            if is_pg:
                where_clauses.append(f'CAST("{filter_col}" AS TEXT) ILIKE %s')
                params.append(f"%{filter_val}%")
            else:
                where_clauses.append(f'CAST("{filter_col}" AS TEXT) LIKE ?')
                params.append(f"%{filter_val}%")

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        tbl_qualified = f'"{schema_name}"."{table_name}"' if is_pg else f'"{table_name}"'

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(columns)

        query = f'SELECT * FROM {tbl_qualified} {where_sql} LIMIT {int(max_limit)}'
        cur = conn.execute(query, tuple(params))
        for r in cur.fetchall():
            row_vals = []
            for col in columns:
                if col.lower() in SENSITIVE_COLUMNS:
                    val = "[REDACTED]"
                else:
                    val = r[col]
                row_vals.append(sanitize_csv_cell(val))
            writer.writerow(row_vals)

    output.seek(0)
    return output.getvalue()
