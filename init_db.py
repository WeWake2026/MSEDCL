"""
Database Initializer supporting both PostgreSQL and SQLite.
Loads schema.sql if running against PostgreSQL, or standard SQLite tables.
Seeds initial admin, manager, quality users, and system settings.
"""

import os
import sys
from werkzeug.security import generate_password_hash
from database import get_db, is_postgres

def init_postgres(conn):
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema.sql")
    if os.path.exists(schema_path):
        with open(schema_path, "r", encoding="utf-8") as f:
            sql_script = f.read()
        cur = conn.cursor()
        # Execute script commands
        for cmd in sql_script.split(";"):
            cmd = cmd.strip()
            if cmd:
                cur.execute(cmd)
        conn.commit()

def init_sqlite(conn):
    cur = conn.cursor()
    # Audits
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        department TEXT NOT NULL,
        call_id TEXT,
        call_date TEXT,
        evaluation_date TEXT,
        advisor_id TEXT,
        advisor_name TEXT NOT NULL,
        team_leader TEXT,
        auditor_name TEXT,
        quality_score REAL,
        is_fatal INTEGER NOT NULL DEFAULT 0,
        fatal_category TEXT,
        error_categories TEXT,
        observation TEXT,
        source_file TEXT,
        import_batch TEXT,
        row_hash TEXT UNIQUE,
        imported_at TEXT NOT NULL,
        audio_url TEXT,
        location TEXT
    )
    """)

    # Warnings
    cur.execute("""
    CREATE TABLE IF NOT EXISTS warnings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        advisor_id TEXT,
        advisor_name TEXT NOT NULL,
        warning_type TEXT NOT NULL,
        issue_date TEXT NOT NULL,
        recipient_email TEXT,
        subject TEXT,
        body TEXT,
        email_status TEXT NOT NULL DEFAULT 'Draft',
        sent_at TEXT,
        provider_message_id TEXT,
        related_audit_ids TEXT,
        created_by TEXT,
        remarks TEXT,
        created_at TEXT NOT NULL,
        department TEXT,
        fatal_count INTEGER DEFAULT 0,
        low_score_count INTEGER DEFAULT 0,
        avg_score REAL,
        issue_category TEXT,
        effective_date TEXT,
        review_date TEXT,
        approved_by TEXT,
        delivery_status TEXT DEFAULT 'Unknown',
        acknowledgement_at TEXT,
        status TEXT DEFAULT 'Open',
        closure_date TEXT,
        closure_remarks TEXT,
        advisor_feedback TEXT,
        advisor_feedback_date TEXT
    )
    """)

    # Import batches
    cur.execute("""
    CREATE TABLE IF NOT EXISTS import_batches (
        id TEXT PRIMARY KEY,
        department TEXT NOT NULL,
        source_file TEXT NOT NULL,
        uploaded_by TEXT NOT NULL,
        uploaded_at TEXT NOT NULL,
        rows_seen INTEGER NOT NULL,
        rows_inserted INTEGER NOT NULL,
        duplicates INTEGER NOT NULL,
        rejected INTEGER NOT NULL,
        report_json TEXT
    )
    """)

    # Users
    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('admin','quality','manager','viewer','advisor')),
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now')),
        last_login TEXT,
        email TEXT,
        full_name TEXT,
        advisor_id TEXT
    )
    """)

    # Email history
    cur.execute("""
    CREATE TABLE IF NOT EXISTS email_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        warning_id INTEGER,
        created_at TEXT NOT NULL,
        employee_id TEXT,
        advisor_name TEXT NOT NULL,
        department TEXT,
        recipient_email TEXT NOT NULL,
        email_type TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        related_audit_ids TEXT,
        created_by TEXT NOT NULL,
        sent_status TEXT NOT NULL DEFAULT 'Draft',
        sent_at TEXT,
        delivery_status TEXT DEFAULT 'Pending Confirmation',
        acknowledgement_at TEXT,
        error_details TEXT
    )
    """)

    # Audit logs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        actor_username TEXT NOT NULL,
        actor_role TEXT NOT NULL,
        action TEXT NOT NULL,
        target_entity TEXT,
        details TEXT,
        ip_address TEXT
    )
    """)

    # System settings
    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    )
    """)

    # Notifications
    cur.execute("""
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        target_role TEXT,
        title TEXT NOT NULL,
        message TEXT NOT NULL,
        notif_type TEXT DEFAULT 'info',
        link_url TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        is_read INTEGER DEFAULT 0,
        created_by TEXT,
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user_read ON notifications (user_id, is_read)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_notifications_role_read ON notifications (target_role, is_read)")
    conn.commit()

def init_db(db_path=None):
    if not is_postgres():
        if not db_path:
            db_path = os.environ.get("DATABASE_PATH", "quality_audit.db")
        parent_dir = os.path.dirname(db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

    with get_db(db_path) as conn:
        if is_postgres():
            init_postgres(conn)
        else:
            init_sqlite(conn)

        cur = conn.cursor()

        # Seed system settings
        defaults = [
            ("company_name", "Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"),
            ("admin_email", "admin.qa@msedcl.in"),
            ("enable_excel_import", "true"),
            ("default_date_range", "30"),
            ("email_provider", "smtp"),
            ("smtp_host", ""),
            ("smtp_port", "587"),
            ("smtp_username", ""),
            ("smtp_password", ""),
            ("smtp_from", "quality.audit@msedcl.in"),
            ("ms_client_id", ""),
            ("ms_tenant_id", ""),
            ("ms_client_secret", ""),
            ("ms_sender_email", ""),
            ("notify_on_fatal", "false"),
            ("notify_on_import", "false")
        ]
        for k, v in defaults:
            try:
                if is_postgres():
                    cur.execute("""
                        INSERT INTO system_settings(setting_key, setting_value, updated_at, updated_by)
                        VALUES (?, ?, NOW(), 'system')
                        ON CONFLICT (setting_key) DO NOTHING
                    """, (k, v))
                else:
                    cur.execute("""
                        INSERT OR IGNORE INTO system_settings(setting_key, setting_value, updated_at, updated_by)
                        VALUES(?,?,datetime('now'),'system')
                    """, (k, v))
            except Exception:
                pass

        # Ensure default seed users exist
        admin_user = os.environ.get("ADMIN_USERNAME", "qaadmin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "Msedcl@123")
        quality_user = os.environ.get("QUALITY_USERNAME", "quality1")
        quality_pass = os.environ.get("QUALITY_PASSWORD", "Msedcl@1234")
        manager_user = os.environ.get("MANAGER_USERNAME", "manager1")
        manager_pass = os.environ.get("MANAGER_PASSWORD", "Msedcl@1235")

        seed_users = [
            (admin_user, admin_pass, 'admin', 'MSEDCL QA Administrator', 'admin.qa@msedcl.in'),
            (quality_user, quality_pass, 'quality', 'Quality Lead', 'quality@msedcl.in'),
            (manager_user, manager_pass, 'manager', 'Operations Manager', 'manager@msedcl.in'),
        ]

        for uname, pwd, role, fname, email in seed_users:
            cur.execute("SELECT id FROM users WHERE username = ?", (uname,))
            existing = cur.fetchone()
            if not existing:
                pwd_hash = generate_password_hash(pwd)
                cur.execute("""
                    INSERT INTO users (username, password_hash, role, full_name, email, active)
                    VALUES (?, ?, ?, ?, ?, 1)
                """, (uname, pwd_hash, role, fname, email))
                print(f"[*] Seeded user account: {uname} ({role})")

        conn.commit()
    engine_name = "PostgreSQL" if is_postgres() else "SQLite"
    print(f"Database initialized successfully with {engine_name} engine.")

if __name__ == "__main__":
    init_db()
