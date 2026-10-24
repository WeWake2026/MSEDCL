# MSEDCL Quality Audit & Performance Management System

An enterprise-grade Quality Assurance, Audit Analytics, Governance, and Disciplinary Performance Tracking application built for **Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)**.

---

## 🌟 Key Functional Modules

### 1. Executive Dashboard & Analytics
- **KPI Metrics**: Total Audits, Inbound & Outbound audit volume, Fatal calls, Low score (<80%), Average score (80%–90%), Good calls (>90%), and Overall Average Quality Score.
- **Dynamic Interactive Visualizations (Chart.js)**:
  - Score Category Distribution (Doughnut).
  - Fatal vs. Non-Fatal Call Ratio (Pie).
  - Inbound vs. Outbound Workload Comparison (Doughnut).
  - Daily & Weekly Audit Volume Timeline (Line).
  - Auditor Evaluation Volumes (Bar).
  - Advisor Quality Performance (Horizontal Bar).
  - Disciplinary Warning & PIP Action Statuses (Bar).
- **Independent Fatal Status**: Compliance/fatal breaches are tracked independently from Quality Score percentages.

### 2. Advanced Export Center
- Individual Excel (`.xlsx`) & CSV exports with professional styling (frozen headers, brand colors, autofilters, optimized column widths):
  1. Inbound Audit Data
  2. Outbound Audit Data
  3. Combined Inbound + Outbound Audits
  4. Fatal Calls Register
  5. Low Score (<80%) Calls
  6. Average Score (80%–90%) Calls
  7. Good Calls (>90%)
  8. Auditor Performance & Evaluation Counts
  9. Advisor-wise Performance Summary
  10. Warning & PIP Action Records
  11. Dispatched Email History Log
  12. Ingestion Batches & Error Log
- **Master Combined Workbook**: Multi-tab formatted Excel sheet bundling all operational registers.
- **Download All Reports ZIP**: Complete package with all individual CSVs, styled Excel files, and Master workbook in one click.

### 3. Advisor 360° Search by Employee ID
- Instant profile lookup by Employee ID or Advisor Name.
- Displays agent identity, assigned departments, supervisors/team leaders, operational center locations, total evaluations, average scores, and fatal counts.
- Shows full interaction history with evaluator remarks and active disciplinary warnings/PIPs.
- Direct single-click filtered Excel export for individual advisor performance reviews.

### 4. Warning & PIP Email Governance
- Multi-tier formal disciplinary actions:
  - *First Warning*, *Second Warning*, *Final Warning*, *Performance Improvement Plan (PIP)*, *Quality Coaching Notice*, and *Quality Feedback Email*.
- Automatic draft generation with linked call interaction IDs, date of occurrence, policy breach category, and improvement targets.
- Confirmation modal before sending to prevent accidental duplicate transmissions.
- Full resolution lifecycle: *Open* ➔ *In Progress* ➔ *Closed* with recorded resolution remarks and closure timestamps.

### 5. Email Transmission History
- Searchable audit log capturing every outbound warning notice.
- Stores recipient email, employee ID, advisor name, notice category, subject, message body, created by, sent timestamp, delivery status, and error diagnostic logs.

### 6. Admin User Management & Role-Based Access Control
- Administrator console to provision user IDs, set roles, and manage permissions.
- **Strict Roles**:
  - `admin`: Full administrative control, user provisioning, global settings, and audit logs.
  - `quality`: Upload audit registers, view reports, draft and dispatch warning notices.
  - `manager`: Full reporting, analytics, drilldowns, and warning approvals.
  - `viewer`: Read-only reporting access.
- Secure PBKDF2/SHA-256 password hashing (plain-text passwords are never stored).
- User activation/deactivation toggles and password reset modal.

### 7. Administrative Settings & Import Controls
- **Enable Excel Import Switch**: Immediate kill-switch to disable workbook ingestion in both the UI and on the server. Previously imported audits remain 100% accessible.
- Organization parameters and admin notification emails.
- Email provider configuration: Standard SMTP or Microsoft Graph OAuth (Azure App Registration).
- Instant SMTP connectivity test ping with diagnostic logging.
- Direct database backup download (`.db`).
- Comprehensive **Administrative Audit Log** tracking every login, import, export, settings update, and email dispatch.

---

## 🚀 Windows PowerShell Quickstart Guide

### 1. Prerequisites
- Python 3.11+ installed and available in your PowerShell path.

### 2. Setup Virtual Environment & Install Dependencies
Open PowerShell in the project directory:

```powershell
# Create virtual environment (if not already present)
py -m venv .venv

# Activate environment
.\.venv\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 3. Configure Environment Variables
Copy `.env.example` to `.env`:

```powershell
Copy-Item .env.example .env
```

Ensure `.env` contains your desired administrative credentials:
```env
APP_SECRET_KEY=msedcl-qa-audit-secret-key-2026-secure
DATABASE_PATH=quality_audit.db
MAX_UPLOAD_MB=25

ADMIN_USERNAME=qaadmin
ADMIN_PASSWORD=YourSecurePasswordHere
QUALITY_USERNAME=quality1
QUALITY_PASSWORD=YourSecurePasswordHere
MANAGER_USERNAME=manager1
MANAGER_PASSWORD=YourSecurePasswordHere
```

### 4. Run Database Migration
Initialize enhanced database tables (`email_history`, `audit_logs`, `system_settings`, and warning columns):

```powershell
.\.venv\Scripts\python.exe migrate_db.py
```

### 5. Run Automated Test Suite
Verify that all 6 test suites pass:

```powershell
.\.venv\Scripts\python.exe test_system.py
```

### 6. Launch the Application
```powershell
.\.venv\Scripts\python.exe app.py
```

Open your browser to:
**[http://127.0.0.1:5000](http://127.0.0.1:5000)**

Log in with:
- **Username**: `qaadmin`
- **Password**: configured in `.env` (default: `Msedcl@123`)

---

## 📧 Email Authentication Guide

### Option A: Organization SMTP (Port 587 with STARTTLS)
1. Go to **Admin Settings** (`/admin/settings`).
2. Set **Email Provider** to `Standard Organization SMTP`.
3. Provide:
   - **SMTP Host**: e.g., `smtp.office365.com` or internal relay host.
   - **Port**: `587`
   - **SMTP Username**: sender mailbox user.
   - **SMTP Password**: account password or Microsoft App Password.
   - **From Address**: e.g., `quality.audit@msedcl.in`.
4. Run a diagnostic test using **Test Email Transmission**.

> **Note on Microsoft Consumer / Enterprise Accounts**: Microsoft 365 and Outlook.com have deprecated Basic Authentication for SMTP AUTH on standard consumer accounts. If your tenant enforces Modern Authentication / MFA, create an **App Password** in Microsoft Security Center or use **Microsoft Graph OAuth** below.

### Option B: Microsoft Graph OAuth (Recommended for Azure / Office 365 Tenants)
1. Register an application in [Azure Portal](https://portal.azure.com) under **App Registrations**.
2. Grant application permission: `Mail.Send`.
3. Copy **Client ID**, **Tenant ID**, and generate a **Client Secret**.
4. Enter these credentials in **Admin Settings** under Microsoft Graph configuration.

---

## 📂 Sample Workbook Templates
- [sample_inbound_audit_template.csv](file:///c:/Users/Vivek/Downloads/MSEDCL_Quality_Audit_System_Starter/MSEDCL_Quality_Audit_System/sample_inbound_audit_template.csv): Sanitized template for customer service inbound calls.
- [sample_outbound_audit_template.csv](file:///c:/Users/Vivek/Downloads/MSEDCL_Quality_Audit_System_Starter/MSEDCL_Quality_Audit_System/sample_outbound_audit_template.csv): Sanitized template for outbound operations and collections audits.

---

## 🔒 Security & Data Privacy Compliance
- Plain-text passwords are never stored; PBKDF2 with SHA-256 is enforced.
- CSRF protection and server-side RBAC guard every route.
- Historical audit data is strictly preserved when users are deactivated or removed.
- No real consumer personal information is hardcoded or exposed.
