# Database Architecture & Operations Guide
## MSEDCL Quality Audit Management System

### 1. Database Architecture Overview

The MSEDCL Quality Audit System features a dual-engine architecture governed by `database.py`:
- **Production Enterprise Engine**: PostgreSQL (supported versions 14, 15, 16, 17, 18) with `psycopg2` thread-safe connection pooling, statement timeouts, and schema isolation.
- **Embedded Local / Fallback Engine**: SQLite 3 with Write-Ahead Logging (`WAL`), 30-second busy timeouts, and PRAGMA foreign key constraints.

The application automatically resolves database connectivity in the following priority order:
1. `DATABASE_URL` environment variable (`postgresql://...`).
2. Discrete environment variables (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_TIMEOUT`).
3. Local SQLite file (`DATABASE_PATH` or `quality_audit.db`).

---

### 2. Discovered Schema & Tables

| Table Name | Description | Key Columns |
|---|---|---|
| `audits` | Individual call evaluation records (Inbound & Outbound) | `id` (PK), `call_id`, `call_date`, `evaluation_date`, `advisor_id`, `advisor_name`, `team_leader`, `auditor_name`, `quality_score`, `is_fatal`, `fatal_category`, `error_categories`, `row_hash` (UNIQUE) |
| `users` | Authenticated users & access control | `id` (PK), `username` (UNIQUE), `password_hash`, `role`, `advisor_id`, `active`, `email`, `full_name` |
| `warnings` | Warning & PIP issuance notices | `id` (PK), `advisor_id`, `advisor_name`, `warning_type`, `issue_date`, `recipient_email`, `status`, `closure_remarks` |
| `email_history` | Audit log of dispatched warning letters & PIP emails | `id` (PK), `warning_id`, `recipient_email`, `email_type`, `sent_status`, `sent_at`, `delivery_status` |
| `import_batches` | Batch logs of Excel/CSV upload sessions | `id` (PK), `department`, `source_file`, `uploaded_by`, `uploaded_at`, `rows_seen`, `rows_inserted`, `duplicates`, `rejected`, `report_json` |
| `audit_logs` | Security and administrative activity trail | `id` (PK), `timestamp`, `actor_username`, `actor_role`, `action`, `target_entity`, `details`, `ip_address` |
| `system_settings` | Key-value application runtime configuration | `setting_key` (PK), `setting_value`, `description`, `updated_at` |

---

### 3. Application Data Ownership & Isolation Rules

- **Advisors / Viewers (`role='advisor'`, `role='viewer'`)**:
  - Bound to their specific `advisor_id` (e.g. `M101403`, `AIPLCC496`).
  - Server-side query filtering strictly constrains views to rows matching `advisor_id = ?`.
  - Prohibited from accessing other advisors' audits, warning records, or system configuration.
  - Access to administrative modules (`/admin/*`, `/upload`, `/settings`) is blocked by role decorators with `HTTP 403 Forbidden`.
- **Quality Auditors (`role='quality'`)**:
  - Can inspect, search, filter, and audit inbound and outbound records across all teams.
  - Can upload new audit workbooks (`/upload`) and trigger warning drafts (`/warnings`).
- **Managers (`role='manager'`)**:
  - Can review reports, view score distributions, review warning status, and manage team performance.
- **System Administrators (`role='admin'`)**:
  - Full operational access to all dashboards, user creation and password resets, system settings, restart controls, and the **Admin Database Explorer**.

---

### 4. Database Roles & Principle of Least Privilege

PostgreSQL privileges must adhere to least-privilege standards:

1. **`postgres` (Master Superuser)**:
   - Used exclusively for database initialization, role provisioning, and OS-level administration.
   - Application connections must **never** run under `postgres`.
2. **`qa_app` (Application Operating Role)**:
   - Restricted to `qa_dashboard` database.
   - Granted `SELECT`, `INSERT`, `UPDATE`, `DELETE` on application tables and sequences in the `public` schema.
   - Prohibited from `SUPERUSER`, `CREATEROLE`, or altering catalog tables.
3. **`qa_readonly` (Optional Audit Explorer Role)**:
   - Granted read-only (`SELECT`) privileges on public tables for external auditing or reporting tools.

---

### 5. Database Explorer Architecture

Integrated inside `/admin/database-explorer`:
- **Server-Side Pagination**: Queries utilize `LIMIT` and `OFFSET` to prevent high memory usage.
- **SQL Injection Prevention**: Table names and schemas are validated strictly against metadata dictionaries (`^[A-Za-z0-9_]+$`). Filter and search parameters use parameterized placeholders (`%s` in PostgreSQL, `?` in SQLite).
- **Column Whitelisting**: Sorting clauses strictly validate column names against inspected table columns.
- **Sensitive Field Masking**: Column names matching `password`, `password_hash`, `token`, `secret`, and `key` are automatically redacted with `•••••••• [REDACTED]` in the interface and `[REDACTED]` in CSV exports.
- **CSV Formula Injection Neutralization**: Exported text fields starting with formula characters (`=`, `+`, `-`, `@`, `\t`, `\r`) are automatically escaped with single quotes (`'`).
