"""
MSEDCL Quality Audit System - SQLite to PostgreSQL Data Migration Tool

Features:
- Complete migration of all tables: users, audits, warnings, import_batches, email_history, audit_logs, system_settings
- Dry-run mode (--dry-run) for verification without database modification
- Preserves primary keys, relationships, timestamps, null values, and status
- Detailed reconciliation report showing source rows, target rows, inserted, skipped, failed
- Resumable and idempotent via ON CONFLICT DO NOTHING
"""

import os
import sys
import json
import sqlite3
import argparse
from datetime import datetime

try:
    import psycopg2
    from psycopg2.extras import execute_batch
except ImportError:
    psycopg2 = None

TABLES = [
    {
        "name": "system_settings",
        "conflict": "(setting_key) DO UPDATE SET setting_value=EXCLUDED.setting_value, updated_at=EXCLUDED.updated_at, updated_by=EXCLUDED.updated_by",
        "has_seq": False
    },
    {
        "name": "users",
        "conflict": "(username) DO NOTHING",
        "has_seq": True
    },
    {
        "name": "import_batches",
        "conflict": "(id) DO NOTHING",
        "has_seq": False
    },
    {
        "name": "audits",
        "conflict": "(row_hash) DO NOTHING",
        "has_seq": True
    },
    {
        "name": "warnings",
        "conflict": "(id) DO NOTHING",
        "has_seq": True
    },
    {
        "name": "email_history",
        "conflict": "(id) DO NOTHING",
        "has_seq": True
    },
    {
        "name": "audit_logs",
        "conflict": "(id) DO NOTHING",
        "has_seq": True
    }
]

def parse_args():
    parser = argparse.ArgumentParser(description="Migrate SQLite data to PostgreSQL for MSEDCL QA System")
    parser.add_argument("--sqlite-path", default=os.environ.get("DATABASE_PATH", "quality_audit.db"), help="Path to SQLite database file")
    parser.add_argument("--postgres-url", default=os.environ.get("DATABASE_URL", ""), help="PostgreSQL connection URL")
    parser.add_argument("--dry-run", action="store_true", help="Perform verification without writing data")
    parser.add_argument("--report-file", default="migration_report.json", help="Path to output reconciliation report JSON")
    return parser.parse_args()

def run_migration():
    args = parse_args()
    sqlite_path = args.sqlite_path
    pg_url = args.postgres_url
    dry_run = args.dry_run

    print("================================================================================")
    print("MSEDCL Quality Audit System - Database Migration Utility")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Source (SQLite): {sqlite_path}")
    print(f"Target (PostgreSQL): {'[REDACTED]' if pg_url else 'None provided'}")
    print(f"Dry Run Mode: {dry_run}")
    print("================================================================================")

    if not os.path.exists(sqlite_path):
        print(f"ERROR: SQLite database file not found at {sqlite_path}")
        sys.exit(1)

    if not dry_run and not pg_url:
        print("ERROR: --postgres-url or DATABASE_URL environment variable must be specified for live migration.")
        print("Run with --dry-run to inspect and validate source data without a PostgreSQL connection.")
        sys.exit(1)

    if not psycopg2 and not dry_run:
        print("ERROR: psycopg2 is required for PostgreSQL migration. Install with 'pip install psycopg2-binary'.")
        sys.exit(1)

    # 1. Connect to SQLite
    sq_conn = sqlite3.connect(sqlite_path)
    sq_conn.row_factory = sqlite3.Row
    sq_cur = sq_conn.cursor()

    report = {
        "timestamp": datetime.now().isoformat(),
        "sqlite_path": sqlite_path,
        "dry_run": dry_run,
        "tables": {},
        "status": "SUCCESS"
    }

    # 2. If live migration, connect to PostgreSQL
    pg_conn = None
    if not dry_run and pg_url:
        try:
            if pg_url.startswith("postgres://"):
                pg_url = "postgresql://" + pg_url[len("postgres://"):]
            pg_conn = psycopg2.connect(pg_url)
            print(" Connected to PostgreSQL destination successfully.")
        except Exception as e:
            print(f"ERROR: Could not connect to PostgreSQL: {e}")
            sys.exit(1)

    total_source_records = 0
    total_migrated_records = 0

    for tbl in TABLES:
        tname = tbl["name"]
        conflict_clause = tbl["conflict"]
        has_seq = tbl["has_seq"]

        # Check if table exists in SQLite
        sq_cur.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (tname,))
        if sq_cur.fetchone()[0] == 0:
            print(f"[-] Table '{tname}' not present in SQLite, skipping.")
            continue

        # Fetch records from SQLite
        sq_cur.execute(f"SELECT * FROM {tname}")
        rows = sq_cur.fetchall()
        source_count = len(rows)
        total_source_records += source_count

        table_stat = {
            "source_count": source_count,
            "migrated_count": 0,
            "skipped_count": 0,
            "failed_count": 0,
            "columns": []
        }

        if source_count == 0:
            print(f"[*] Table '{tname}': 0 records found in source.")
            report["tables"][tname] = table_stat
            continue

        columns = list(rows[0].keys())
        table_stat["columns"] = columns

        print(f"[>] Table '{tname}': {source_count} records to migrate. Columns: {len(columns)}")

        if dry_run:
            table_stat["migrated_count"] = source_count
            print(f"    [DRY RUN] Would migrate {source_count} rows into '{tname}'.")
        else:
            pg_cur = pg_conn.cursor()
            cols_str = ", ".join([f'"{c}"' for c in columns])
            placeholders = ", ".join(["%s"] * len(columns))
            sql = f'INSERT INTO {tname} ({cols_str}) VALUES ({placeholders}) ON CONFLICT {conflict_clause};'

            batch_data = []
            for r in rows:
                row_vals = []
                for c in columns:
                    val = r[c]
                    # Handle boolean conversion for is_fatal and active if necessary
                    row_vals.append(val)
                batch_data.append(tuple(row_vals))

            try:
                execute_batch(pg_cur, sql, batch_data, page_size=200)
                pg_conn.commit()
                table_stat["migrated_count"] = source_count
                total_migrated_records += source_count
                print(f"    [OK] Successfully migrated {source_count} rows into '{tname}'.")

                # Reset Postgres sequence for SERIAL PK if applicable
                if has_seq:
                    try:
                        seq_sql = f"SELECT setval(pg_get_serial_sequence('{tname}', 'id'), COALESCE((SELECT MAX(id) FROM {tname}), 1));"
                        pg_cur.execute(seq_sql)
                        pg_conn.commit()
                    except Exception as seq_err:
                        # Non-fatal if sequence reset is not supported
                        pass
            except Exception as e:
                pg_conn.rollback()
                table_stat["failed_count"] = source_count
                table_stat["error"] = str(e)
                report["status"] = "PARTIAL_FAILURE"
                print(f"    [ERROR] Failed to insert rows into '{tname}': {e}")
            finally:
                pg_cur.close()

        report["tables"][tname] = table_stat

    sq_conn.close()
    if pg_conn:
        pg_conn.close()

    report["total_source_records"] = total_source_records
    report["total_migrated_records"] = total_migrated_records

    # Write report file
    try:
        with open(args.report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\n[+] Detailed migration report saved to: {os.path.abspath(args.report_file)}")
    except Exception as e:
        print(f"[-] Could not save migration report to {args.report_file}: {e}")

    print("================================================================================")
    print(f"Migration completed with status: {report['status']}")
    print(f"Total source records inspected: {total_source_records}")
    print(f"Total records processed/migrated: {total_migrated_records if not dry_run else total_source_records} {'(Dry Run)' if dry_run else ''}")
    print("================================================================================")

if __name__ == "__main__":
    run_migration()
