# Application Security Policy & Audit Report

This document details the security model, vulnerability mitigation, and operational controls implemented for the **MSEDCL Quality Audit System**.

---

## 1. Security Architecture & Controls

### Authentication & Password Hashing
- **Algorithm**: Werkzeug PBKDF2 with SHA-256 (salted and stretched, minimum 260,000 rounds).
- **Session Security**:
  - `SESSION_COOKIE_HTTPONLY = True`: Blocks JavaScript cookie access to prevent XSS session hijacking.
  - `SESSION_COOKIE_SAMESITE = 'Lax'`: Protects against Cross-Site Request Forgery (CSRF).
  - Cryptographically secure session tokens generated via `secrets.token_hex(32)`.

### Role-Based Access Control (RBAC)
Server-side authorization enforced via `@roles(...)` decorator on every protected endpoint:
- **`admin`**: Full system control, user provisioning, role assignments, system settings, purge actions.
- **`manager`**: Quality analytics, audits review, warning oversight, reporting exports, user status management.
- **`quality`**: Call auditing, Excel dataset uploads, warning draft generation, advisor evaluations.
- **`viewer` / `advisor`**: Restricted strictly to own advisor record (`advisor_id` scoping). Blocked from viewing other advisors' audits, emails, or warning letters.

### SQL Injection Protection
- **Parameterized Queries**: All database operations use parameterized binding (`?` on SQLite, `%s` on PostgreSQL). Raw string concatenation in SQL statements is strictly prohibited.
- **Universal Cursor**: Normalizes datetime functions and query placeholders automatically without manual dynamic string manipulation.

### Security Headers & Browser Hardening
Implemented on all HTTP responses via `@app.after_request`:
- `X-Content-Type-Options: nosniff`: Prevents MIME-sniffing exploits.
- `X-Frame-Options: SAMEORIGIN`: Prevents clickjacking attacks.
- `X-XSS-Protection: 1; mode=block`: Activates reflective XSS filters.
- `Referrer-Policy: strict-origin-when-cross-origin`: Restricts referrer leakage.

### File Upload Security
- Strict extension whitelist: `.xlsx`, `.xlsm`, `.csv`.
- File content size capped via `MAX_CONTENT_LENGTH` (default 25 MB).
- File paths sanitized using `secure_filename`.

---

## 2. Secrets Management & Environment Isolation

- No hardcoded database credentials or production secrets are stored in Git.
- Real credentials must be injected via `.env` or cloud secret managers (e.g. Render, AWS Secrets Manager).
- Sample configurations are provided in `.env.example` with placeholders only.

---

## 3. Audit Logging

Every critical event is logged into the `audit_logs` table:
- Actor username and assigned role
- Action timestamp
- Target entity and operation type
- Source IP address

---

## 4. Reporting Security Vulnerabilities

To report a vulnerability or security concern, contact the MSEDCL Quality Security Team at `admin.qa@msedcl.in`.
