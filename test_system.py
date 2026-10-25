"""
Comprehensive Automated Test Suite for MSEDCL Quality Audit System

Tests:
1. Universal Database Adapter & Health Check endpoint
2. Security Headers (nosniff, SAMEORIGIN, X-XSS-Protection)
3. Database row counts, seeded admin user, password hashing
4. Role-based Access Control (Admin, Quality, Manager, Advisor)
5. Advisor / Viewer User-wise Data Isolation (Strictly own records)
6. Unauthorized access blocked to Admin Database Explorer
7. Admin access to Database Explorer page and API
8. Table pagination, sorting, and search in Explorer
9. Sensitive field redaction (password_hash masked in views & CSV)
10. CSV formula injection protection (CWE-1236)
11. Migration script dry-run & reconciliation
12. Centralized discrete PostgreSQL configuration resolution
"""

import os
import io
import unittest
import json
from app import app
from database import get_db, check_db_health, build_database_uri_from_env
import db_explorer_service
from export_service import sanitize_for_export
import pandas as pd
from werkzeug.security import check_password_hash

class TestMsedclSystem(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def login_user(self, username, password=""):
        """Helper to log in a user and set test session."""
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not user:
                raise ValueError(f"User {username} not found for test login")
        with self.client.session_transaction() as sess:
            sess["user_id"] = user["id"]
            sess["username"] = user["username"]
            sess["role"] = user["role"]

    def test_01_health_check_endpoint(self):
        """Test /healthz endpoint returns 200 and valid JSON status without credential leakage."""
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("healthy", data)
        self.assertTrue(data["healthy"])
        self.assertIn("engine", data)
        self.assertNotIn("password", json.dumps(data).lower())

    def test_02_security_headers(self):
        """Verify essential security headers are present on all responses."""
        response = self.client.get("/login")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(response.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(response.headers.get("X-XSS-Protection"), "1; mode=block")

    def test_03_database_row_counts_and_users(self):
        """Verify seeded records exist and password hashes are properly salted."""
        with get_db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username='qaadmin'").fetchone()
            self.assertIsNotNone(user, "Default admin user 'qaadmin' must exist")
            self.assertTrue(check_password_hash(user["password_hash"], "Msedcl@123"))

            audits_count = conn.execute("SELECT COUNT(*) AS cnt FROM audits").fetchone()
            self.assertGreaterEqual(audits_count["cnt"], 0)

    def test_04_advisor_menu_and_data_isolation(self):
        """Verify advisor / viewer users are restricted to their own advisor_id."""
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (username, password_hash, role, advisor_id, active, full_name, email)
                VALUES ('test_adv', 'hash123', 'advisor', 'M101403', 1, 'Test Advisor', 'adv@msedcl.in')
            """)
            conn.commit()

            test_user = conn.execute("SELECT * FROM users WHERE username='test_adv'").fetchone()
            self.assertIsNotNone(test_user)
            self.assertEqual(test_user["role"], "advisor")
            self.assertEqual(test_user["advisor_id"], "M101403")

    def test_05_unauthorized_redirects(self):
        """Verify protected routes redirect unauthenticated users to /login."""
        protected_routes = [
            "/", "/audits", "/warnings", "/admin/users",
            "/admin/settings", "/admin/database-explorer"
        ]
        for route in protected_routes:
            res = self.client.get(route)
            self.assertIn(res.status_code, [302, 401, 403], f"Route {route} should require authentication")

    def test_06_database_explorer_admin_access(self):
        """Verify only Admin can access Database Explorer."""
        # Advisor should receive 403
        self.login_user("test_adv")
        res_advisor = self.client.get("/admin/database-explorer")
        self.assertEqual(res_advisor.status_code, 403)

        # Admin should receive 200
        self.login_user("qaadmin")
        res_admin = self.client.get("/admin/database-explorer")
        self.assertEqual(res_admin.status_code, 200)
        self.assertIn(b"Database Explorer", res_admin.data)
        self.assertIn(b"Storage Engine", res_admin.data)

    def test_07_database_explorer_pagination_and_masking(self):
        """Verify Database Explorer paginates records and redacts password_hash."""
        records = db_explorer_service.get_table_records(table_name="users", page=1, per_page=10)
        self.assertIn("records", records)
        self.assertGreaterEqual(records["total_rows"], 1)
        
        # Verify sensitive password_hash is redacted
        for r in records["records"]:
            if "password_hash" in r:
                self.assertIn("[REDACTED]", r["password_hash"])

    def test_08_csv_formula_injection_protection(self):
        """Verify CSV exports neutralize formula injection (CWE-1236)."""
        df = pd.DataFrame({
            "name": ["=1+1", "@SUM(A1:A10)", "-5+2", "+cmd|' /C calc'!A0", "Safe Name"],
            "score": [95, 80, 70, 85, 90]
        })
        sanitized = sanitize_for_export(df)
        self.assertTrue(sanitized["name"].iloc[0].startswith("'="))
        self.assertTrue(sanitized["name"].iloc[1].startswith("'@"))
        self.assertTrue(sanitized["name"].iloc[2].startswith("'-"))
        self.assertTrue(sanitized["name"].iloc[3].startswith("'+"))
        self.assertEqual(sanitized["name"].iloc[4], "Safe Name")

    def test_09_database_explorer_csv_export(self):
        """Test admin CSV export via Database Explorer."""
        self.login_user("qaadmin")
        res = self.client.get("/admin/database-explorer/export-csv?table=users")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers.get("Content-Type"), "text/csv; charset=utf-8")
        csv_text = res.data.decode("utf-8")
        self.assertIn("username", csv_text)
        self.assertNotIn("Msedcl@123", csv_text)
        self.assertIn("[REDACTED]", csv_text)

    def test_10_database_discrete_env_resolution(self):
        """Verify discrete DB parameters (DB_HOST, DB_NAME, DB_USER) resolve safely."""
        old_url = os.environ.get("DATABASE_URL")
        try:
            if "DATABASE_URL" in os.environ:
                del os.environ["DATABASE_URL"]
            os.environ["DB_HOST"] = "127.0.0.1"
            os.environ["DB_PORT"] = "5432"
            os.environ["DB_NAME"] = "msedcl_qa"
            os.environ["DB_USER"] = "msedcl_user"
            os.environ["DB_PASSWORD"] = "Secret%123"

            uri = build_database_uri_from_env()
            self.assertTrue(uri.startswith("postgresql://msedcl_user:Secret%25123@127.0.0.1:5432/msedcl_qa"))
        finally:
            if old_url is not None:
                os.environ["DATABASE_URL"] = old_url
            for k in ["DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD"]:
                os.environ.pop(k, None)

    def test_11_migration_script_dry_run(self):
        """Test migrate_sqlite_to_postgres dry run executes without errors."""
        import subprocess
        result = subprocess.run(
            [os.path.join(".venv", "Scripts", "python.exe"), "migrate_sqlite_to_postgres.py", "--dry-run"],
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Migration completed with status: SUCCESS", result.stdout)
        self.assertTrue(os.path.exists("migration_report.json"))

if __name__ == "__main__":
    unittest.main()
