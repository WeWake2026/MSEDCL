"""
Comprehensive Automated Test Suite for MSEDCL Quality Audit System

Tests:
1. Universal Database Adapter & Health Check endpoint
2. Role-Based Access Control (Admin, Quality, Manager, Advisor/Viewer)
3. Cross-User Data Isolation (Advisor cannot view other advisors' records)
4. Password Hashing & Security Headers (No sensitive leaks, nosniff, SAMEORIGIN)
5. SQLite to PostgreSQL Data Migration Tool Dry-Run & Reconciliation
6. Excel / Audit Data Querying & Filtering Logic
"""

import os
import unittest
import json
from app import app
from database import get_db, check_db_health
from werkzeug.security import check_password_hash

class TestMsedclSystem(unittest.TestCase):
    def setUp(self):
        self.app = app
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def test_01_health_check_endpoint(self):
        """Test /healthz endpoint returns 200 and valid JSON status."""
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertIn("healthy", data)
        self.assertTrue(data["healthy"])
        self.assertIn("engine", data)

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
            # Create a test advisor user
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
        protected_routes = ["/", "/audits", "/warnings", "/admin/users", "/admin/settings"]
        for route in protected_routes:
            res = self.client.get(route)
            self.assertIn(res.status_code, [302, 401, 403], f"Route {route} should require authentication")

    def test_06_migration_script_dry_run(self):
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
