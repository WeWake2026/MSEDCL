import unittest
import urllib.request, urllib.parse, http.cookiejar, json

class TestMSEDCLSystem(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_url = "http://127.0.0.1:5000"
        cls.cj = http.cookiejar.CookieJar()
        cls.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cls.cj))
        
        # Authenticate as Admin
        data = urllib.parse.urlencode({"username": "qaadmin", "password": "Msedcl@123"}).encode('utf-8')
        resp = cls.opener.open(f"{cls.base_url}/login", data=data)
        assert resp.status == 200

    def test_01_dashboard_and_analytics(self):
        resp = self.opener.open(f"{self.base_url}/")
        html = resp.read().decode('utf-8')
        self.assertEqual(resp.status, 200)
        self.assertIn("MSEDCL Quality Audit & Performance Dashboard", html)
        self.assertIn("Total Audited Calls", html)
        self.assertIn("Fatal Call Errors", html)
        self.assertTrue("Low Score" in html)
        self.assertIn("Average Score (80-90%)", html)
        self.assertTrue("Good Calls" in html)
        self.assertIn("scoreDistChart", html)
        self.assertIn("trendChart", html)
        self.assertIn("auditorChart", html)

    def test_02_export_center_and_downloads(self):
        # View Export Center
        resp = self.opener.open(f"{self.base_url}/export-center")
        html = resp.read().decode('utf-8')
        self.assertIn("Advanced Export Center", html)
        self.assertIn("Combined Master Workbook", html)
        self.assertIn("Download All Reports ZIP", html)

        # Test single Excel export
        resp_xl = self.opener.open(f"{self.base_url}/export/single/Inbound%20Audits?format=xlsx")
        self.assertEqual(resp_xl.status, 200)
        self.assertTrue(len(resp_xl.read()) > 1000)

        # Test single CSV export
        resp_csv = self.opener.open(f"{self.base_url}/export/single/Fatal%20Calls?format=csv")
        self.assertEqual(resp_csv.status, 200)
        self.assertTrue(len(resp_csv.read()) > 100)

        # Test Master Combined Excel
        resp_comb = self.opener.open(f"{self.base_url}/export/combined")
        self.assertEqual(resp_comb.status, 200)
        self.assertTrue(len(resp_comb.read()) > 5000)

        # Test All Reports ZIP Archive
        resp_zip = self.opener.open(f"{self.base_url}/export/zip")
        self.assertEqual(resp_zip.status, 200)
        self.assertTrue(len(resp_zip.read()) > 5000)

    def test_03_advisor_search(self):
        resp = self.opener.open(f"{self.base_url}/advisor-search?emp_id=AIPLCC010")
        html = resp.read().decode('utf-8')
        self.assertEqual(resp.status, 200)
        self.assertIn("Search Advisor by Employee ID", html)
        self.assertIn("ALPESH MAROTRAO DAHAKE", html)
        self.assertIn("Audited Interactions", html)

    def test_04_warnings_and_email_history(self):
        # 1. Create a draft warning
        warn_data = urllib.parse.urlencode({
            "advisor_name": "Test Advisor Automation",
            "advisor_id": "TEST999",
            "department": "Inbound",
            "warning_type": "First Warning",
            "issue_date": "2026-10-24",
            "recipient_email": "test.advisor@msedcl.in",
            "subject": "Automated Quality Breach Notice",
            "body": "Your quality score on interaction #123 was below standard.",
            "remarks": "Call ID #123",
            "status": "Open"
        }).encode('utf-8')
        post_resp = self.opener.open(f"{self.base_url}/warnings", data=warn_data)
        self.assertEqual(post_resp.status, 200)

        # 2. Check Warnings page
        resp = self.opener.open(f"{self.base_url}/warnings")
        html = resp.read().decode('utf-8')
        self.assertIn("Test Advisor Automation", html)

        # 3. Check Email History page
        resp_eh = self.opener.open(f"{self.base_url}/email-history")
        html_eh = resp_eh.read().decode('utf-8')
        self.assertIn("Warning & PIP Email Transmission History", html_eh)

    def test_05_admin_user_management(self):
        resp = self.opener.open(f"{self.base_url}/admin/users")
        html = resp.read().decode('utf-8')
        self.assertEqual(resp.status, 200)
        self.assertIn("User Access & Role Management", html)
        self.assertIn("qaadmin", html)

    def test_06_admin_settings_and_logs(self):
        resp = self.opener.open(f"{self.base_url}/admin/settings")
        html = resp.read().decode('utf-8')
        self.assertEqual(resp.status, 200)
        self.assertIn("System & Security Settings", html)
        self.assertIn("Excel Workbook Import Switch", html)

        # Check Audit Logs
        resp_logs = self.opener.open(f"{self.base_url}/admin/audit-logs")
        html_logs = resp_logs.read().decode('utf-8')
        self.assertIn("System & Administrative Audit Logs", html_logs)

if __name__ == "__main__":
    unittest.main()
