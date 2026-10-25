# Security Audit & Hardening Report
## MSEDCL Quality Audit Management System

### 1. Executive Summary

This security audit reviews the existing codebase, database adapters, authentication workflows, file upload ingestion, export services, and the new **Admin Database Explorer**.

| Security Domain | Status Before Upgrade | Hardening Implemented |
|---|---|---|
| Database Configuration | Only `DATABASE_URL` supported; no discrete host/port resolution | Dual configuration: `DATABASE_URL` or discrete parameters (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`) with statement timeouts |
| CSV Formula Injection | Risk of formula injection (CWE-1236) in reports & exports | Implemented `sanitize_csv_cell()` and `sanitize_for_export()` escaping leading `=`, `+`, `-`, `@`, `\t`, `\r` |
| Database Introspection | No interface for DB health & schema viewing | Implemented admin-only Database Explorer with server-side pagination, sorting whitelists, and sensitive column masking |
| Sensitive Data Redaction | Raw password hashes could potentially leak in table dumps | Explicit masking (`[REDACTED]`) for fields matching passwords, secrets, tokens, and hashes |
| Database Privileges | Single connection string without least privilege documentation | Enforced separation of master `postgres` role and application `msedcl_user` role; documentation of `msedcl_readonly` role |
| User Data Isolation | Advisors could theoretically see other rows if not filtered | Enforced server-side `advisor_id` scoping across queries, views, and exports; 403 Forbidden on admin endpoints |
| Secret Storage | Raw credentials in `.gitignore` needed broader coverage | Enhanced `.gitignore` to block `.env`, `.env.*`, `*.db`, `*.dump`, `*.key`, `*.pem`, `dump_*.sql` |

---

### 2. Detailed Vulnerability Analyses & Hardening

#### A. CSV & Spreadsheet Formula Injection (CWE-1236)
- **Vulnerability**: Spreadsheet applications (Microsoft Excel, LibreOffice Calc) interpret cell values starting with `=`, `+`, `-`, `@` as active calculation formulas or DDE execution commands. If user-submitted fields (e.g. employee names, observations, remarks) begin with these characters, opening an exported CSV or Excel report could trigger unintended formula execution.
- **Resolution**: Both `export_service.py` and `db_explorer_service.py` now run all export strings through a sanitization function that prepends a single quotation mark (`'`) to neutralise formula triggers.

#### B. SQL Injection (SQLi) Defense in Database Explorer
- **Vulnerability**: Dynamic table inspection and record browsing often suffer from string concatenation when building `SELECT * FROM <table> WHERE <col> LIKE <search>`.
- **Resolution**:
  - Table and schema identifiers are strictly validated against regex `^[A-Za-z0-9_]+$` and verified against `information_schema.tables` / `sqlite_master`.
  - Column sorting parameters are strictly verified against whitelist arrays derived from table metadata.
  - Search queries use parameterized placeholders (`%s` for PostgreSQL, `?` for SQLite).

#### C. Sensitive Field Redaction
- **Vulnerability**: Displaying raw database records to administrators could expose password hashes or sensitive authentication tokens in HTTP responses.
- **Resolution**: Column names matching `password`, `password_hash`, `token`, `secret`, `api_key`, `access_token` are automatically replaced with `•••••••• [REDACTED]` in the UI and `[REDACTED]` in CSV exports.

#### D. Authentication & Session Security
- Passwords are encrypted using Werkzeug PBKDF2 with SHA-256 and unique per-user cryptographic salts.
- Sessions use `HttpOnly=True`, `SameSite='Lax'`, and secure cookie configurations.
- HTTP security headers are injected into all HTTP responses:
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: SAMEORIGIN`
  - `X-XSS-Protection: 1; mode=block`
  - `Referrer-Policy: strict-origin-when-cross-origin`

---

### 3. Verification Testing
The hardened controls were verified via automated unit tests in `test_system.py`:
- `test_06_database_explorer_admin_access`: Confirmed advisor role receives HTTP 403 when requesting `/admin/database-explorer`.
- `test_07_database_explorer_pagination_and_masking`: Confirmed `password_hash` column is redacted.
- `test_08_csv_formula_injection_protection`: Confirmed leading `=`, `+`, `-`, `@` values are properly escaped.
- `test_09_database_explorer_csv_export`: Confirmed admin CSV export contains redacted password hashes.
- `test_10_database_discrete_env_resolution`: Confirmed discrete parameters correctly assemble connection URI.
