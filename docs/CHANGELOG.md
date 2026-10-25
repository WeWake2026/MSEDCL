# Changelog
All notable changes to the MSEDCL Quality Audit Management System are documented in this file.

## [2.2.0] - Notification Management & Broadcast Center

### Added
- **Notification & Broadcast Center (`/notifications`)**:
  - Dispatch notifications targeted to **All Users** (`target_scope='all'`), **Specific Roles** (`advisor`, `quality`, `manager`), or **Individual User Accounts** (`user_id`).
  - Priority & status badges: Informational (Blue), Quality Warning (Amber), Urgent Compliance Alert (Red), and Milestone / Appreciation (Green).
  - Optional action URLs (`link_url`) to deep-link directly into relevant audit tables, warnings, or reports.
  - Granular permissions: Admins, Managers, and Quality Leads can dispatch; Advisors and Viewers receive notifications targeted to them or broadcasted to everyone.
  - Mark single notification as read (`/notifications/<id>/read`) or batch "Mark All as Read" (`/notifications/mark-all-read`).
  - Delete notification authorization for Admins & Managers (`/notifications/<id>/delete`).
  - Dynamic topbar notification bell with live red unread count badge and quick-view dropdown menu.
  - Sidebar navigation links added for both Advisor views (`My Actions & Records`) and Manager/Quality views (`GOVERNANCE & ACTIONS`).
- **Database Schema**:
  - Added `notifications` table in [schema.sql](file:///c:/Users/Vivek/Downloads/MSEDCL_Quality_Audit_System_Starter/MSEDCL_Quality_Audit_System/schema.sql) with indexed queries on `(user_id, is_read)` and `(target_role, is_read)`.

## [2.1.0] - Upgrade Release: PostgreSQL, Database Explorer & Security Hardening

### Added
- **Admin Database Explorer**:
  - Live database engine and connectivity status card displaying active engine (PostgreSQL / SQLite), database name, and server version.
  - Interactive table navigator listing accessible tables, views, and row count estimates without scanning large tables.
  - Paginated record viewer supporting configurable page sizes (15, 25, 50, 100), sortable columns, and cross-column parameterized search.
  - Automatic sensitive-field redaction: column names matching `password`, `password_hash`, `token`, `secret`, and `key` are displayed as `•••••••• [REDACTED]` and masked in exports.
  - Table schema and index inspector detailing column names, datatypes, nullability flags, defaults, and primary key constraints.
  - Filtered table CSV export with safe file naming and formula injection prevention.
  - New service module `db_explorer_service.py` separating business logic and database queries from presentation routes.
- **Enhanced Database Connectivity**:
  - Centralized connection management supporting discrete parameters: `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, and `DB_TIMEOUT` in addition to unified `DATABASE_URL`.
  - Added connection statement timeout controls (`statement_timeout=15000`) and graceful failure handling.
  - Added `reset_pg_pool()` helper for safe connection recycling.
- **Formula Injection (CWE-1236) Protection**:
  - Added cell-level formula sanitization (`sanitize_csv_cell`) and dataframe column sanitization (`sanitize_for_export`) prepending single quotes to values starting with `=`, `+`, `-`, `@`, `\t`, or `\r`.
- **Automated Test Suite Expansion**:
  - Extended `test_system.py` from 6 tests to 11 automated test cases verifying health check integrity, security headers, database row counts, RBAC isolation, Database Explorer access, pagination, sensitive masking, CSV formula sanitization, discrete environment resolution, and migration dry-runs.
- **Documentation**:
  - Created `SETUP_GUIDE.md` with exact CMD commands for Windows.
  - Created `DATABASE_GUIDE.md` documenting schema, data ownership rules, and least privilege roles.
  - Created `SECURITY_AUDIT.md` detailing security fixes and threat models.
  - Updated `README.md` and `.env.example`.

### Changed
- Updated `database.py` with discrete connection variable builder, timeouts, and health check version extraction.
- Updated `templates/layout.html` to integrate the Database Explorer (`/admin/database-explorer`) directly into the Admin sidebar navigation.
- Hardened `.gitignore` to prevent committing `.env`, `*.dump`, `*.key`, `*.pem`, and local database backups.

### Security
- Resolved CSV formula injection vulnerability across all export pathways.
- Enforced strict whitelist validation for dynamic table and column sorting in Database Explorer.
- Masked sensitive password hashes in record viewing and export endpoints.
- Enforced role-based access control prohibiting non-admin users from accessing Database Explorer.
