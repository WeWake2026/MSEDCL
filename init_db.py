import os, sqlite3
from werkzeug.security import generate_password_hash

def init_db(db_path=None):
    if not db_path:
        db_path = os.environ.get("DATABASE_PATH", "quality_audit.db")

    # Ensure parent directory exists if path includes directories
    parent_dir = os.path.dirname(db_path)
    if parent_dir and not os.path.exists(parent_dir):
        os.makedirs(parent_dir, exist_ok=True)

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # 1. Base tables
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

    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    )
    """)

    # 2. Seed default system settings
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
        cur.execute("INSERT OR IGNORE INTO system_settings(setting_key, setting_value, updated_at, updated_by) VALUES(?,?,datetime('now'),'system')", (k, v))

    # 3. Seed default admin, quality, and manager users if table empty
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    if count == 0:
        admin_user = os.environ.get("ADMIN_USERNAME", "qaadmin")
        admin_pass = os.environ.get("ADMIN_PASSWORD", "Msedcl@123")
        quality_user = os.environ.get("QUALITY_USERNAME", "quality1")
        quality_pass = os.environ.get("QUALITY_PASSWORD", "Msedcl@1234")
        manager_user = os.environ.get("MANAGER_USERNAME", "manager1")
        manager_pass = os.environ.get("MANAGER_PASSWORD", "Msedcl@1235")

        seed_users = [
            (admin_user, generate_password_hash(admin_pass), 'admin', 'MSEDCL QA Administrator', 'admin.qa@msedcl.in'),
            (quality_user, generate_password_hash(quality_pass), 'quality', 'Quality Lead', 'quality@msedcl.in'),
            (manager_user, generate_password_hash(manager_pass), 'manager', 'Operations Manager', 'manager@msedcl.in'),
        ]
        for u in seed_users:
            cur.execute("""
                INSERT INTO users (username, password_hash, role, full_name, email, active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, datetime('now'))
            """, u)

    conn.commit()
    conn.close()
    print(f"Database initialized successfully at {db_path}.")

if __name__ == "__main__":
    init_db()
