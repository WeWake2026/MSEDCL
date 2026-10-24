import sqlite3, os

def run_migrations(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    
    # Check users table columns
    cur.execute("PRAGMA table_info(users)")
    user_cols = [c[1] for c in cur.fetchall()]
    
    if "created_at" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN created_at TEXT")
    if "last_login" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN last_login TEXT")
    if "email" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN email TEXT")
    if "full_name" not in user_cols:
        cur.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        
    # Check warnings table columns
    cur.execute("PRAGMA table_info(warnings)")
    warn_cols = [c[1] for c in cur.fetchall()]
    
    additional_warn_cols = [
        ("department", "TEXT"),
        ("fatal_count", "INTEGER DEFAULT 0"),
        ("low_score_count", "INTEGER DEFAULT 0"),
        ("avg_score", "REAL"),
        ("issue_category", "TEXT"),
        ("effective_date", "TEXT"),
        ("review_date", "TEXT"),
        ("approved_by", "TEXT"),
        ("delivery_status", "TEXT DEFAULT 'Unknown'"),
        ("acknowledgement_at", "TEXT"),
        ("status", "TEXT DEFAULT 'Open'"),  # Open, In Progress, Closed
        ("closure_date", "TEXT"),
        ("closure_remarks", "TEXT"),
        ("advisor_feedback", "TEXT"),
        ("advisor_feedback_date", "TEXT")
    ]
    for col_name, col_type in additional_warn_cols:
        if col_name not in warn_cols:
            cur.execute(f"ALTER TABLE warnings ADD COLUMN {col_name} {col_type}")
            
    # Check audits table columns for location / department consistency
    cur.execute("PRAGMA table_info(audits)")
    audit_cols = [c[1] for c in cur.fetchall()]
    if "location" not in audit_cols:
        cur.execute("ALTER TABLE audits ADD COLUMN location TEXT")

    # Create email_history table
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
        sent_status TEXT NOT NULL DEFAULT 'Draft', -- Draft, Sent, Failed
        sent_at TEXT,
        delivery_status TEXT DEFAULT 'Pending Confirmation',
        acknowledgement_at TEXT,
        error_details TEXT
    )
    """)

    # Create audit_logs table for administrative / security actions
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

    # Create system_settings table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        setting_key TEXT PRIMARY KEY,
        setting_value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL
    )
    """)

    # Seed default system settings if not present
    defaults = [
        ("company_name", "Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"),
        ("admin_email", "admin.qa@msedcl.in"),
        ("enable_excel_import", "true"),
        ("default_date_range", "30"),
        ("email_provider", "smtp"), # smtp or ms_graph
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

    # Update existing users created_at if null
    cur.execute("UPDATE users SET created_at = datetime('now') WHERE created_at IS NULL")

    conn.commit()
    conn.close()
    print("Schema migration completed successfully.")

if __name__ == "__main__":
    db_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quality_audit.db")
    run_migrations(db_file)
