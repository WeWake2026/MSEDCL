<div align="center">

# ⚡ Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)
### Enterprise Quality Audit & Performance Analytics Management Portal

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0.3-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14%2B-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://postgresql.org)
[![SQLite](https://img.shields.io/badge/SQLite-Fallback-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Chart.js](https://img.shields.io/badge/Chart.js-4.4.1-FF6384?style=for-the-badge&logo=chartdotjs&logoColor=white)](https://chartjs.org)
[![Render](https://img.shields.io/badge/Render-Deploy%20Ready-46E3B7?style=for-the-badge&logo=render&logoColor=white)](https://render.com)
[![Status](https://img.shields.io/badge/Quality%20System-Production%20Ready-success?style=for-the-badge)]()

<br/>

**A mission-critical Quality Assurance, Contact Center Analytics, Disciplinary Warning/PIP Governance, and Multi-department Audit portal engineered for MSEDCL.**

---

</div>

## 📑 Table of Contents
- [✨ Core Highlights & Features](#-core-highlights--features)
- [🗄️ PostgreSQL Database Integration & Architecture](#️-postgresql-database-integration--architecture)
- [🔄 Data Migration Guide (SQLite to PostgreSQL)](#-data-migration-guide-sqlite-to-postgresql)
- [👥 Role-Based Access Control (RBAC)](#-role-based-access-control-rbac)
- [🚀 Quick Start & Installation](#-quick-start--installation)
- [🌐 Cloud Deployment (Render / Gunicorn)](#-cloud-deployment-render--gunicorn)
- [🛡️ Security Controls & Auditing](#️-security-controls--auditing)
- [🧪 Automated Testing & Verification](#-automated-testing--verification)
- [📚 Documentation Index](#-documentation-index)

---

## ✨ Core Highlights & Features

| Capability | Description |
| :--- | :--- |
| **🐘 Production PostgreSQL Integration** | High-performance pooled database adapter supporting Render Postgres, Supabase, AWS RDS, Docker, and automatic SQLite fallback. |
| **⚡ Live Ingestion & Auditing** | Seamless `.xlsx`, `.xlsm`, and `.csv` audit file ingestion with SHA-256 deduplication and instant batch reports. |
| **🎧 Direct Audio Playback** | Embedded audio players directly in audit tables and score reports for recorded calls. |
| **🛡️ Disciplinary Warning & PIPs** | Multi-tier corrective notice generator with draft email transmission, acknowledgement tracking, and admin controls. |
| **👥 User & Advisor Scoping** | Advisors are restricted strictly to their own audit records, fatal calls, and warning notices. |
| **🌓 Day / Night Themes** | Dynamic dark/light mode toggle with persistent local preferences and contrast compliance. |
| **🩺 Health Check API** | Built-in `/healthz` and `/api/health` endpoints returning database engine type, latency, and status. |

---

## 🗄️ PostgreSQL Database Integration & Architecture

The application includes a universal connection abstraction layer ([database.py](file:///database.py)) that provides:
- **Connection Pooling**: Uses `psycopg2.pool.ThreadedConnectionPool` with configurable min/max connection bounds (`DB_POOL_MIN`, `DB_POOL_MAX`).
- **Dynamic Query Normalization**: Transparently handles parameter conversion (`?` vs `%s`) and date functions (`datetime('now')` vs `NOW()`).
- **Graceful Fallback**: Automatically connects to SQLite (`quality_audit.db`) for local developer setups without a local PostgreSQL daemon.

See [DATABASE_SETUP.md](file:///DATABASE_SETUP.md) for step-by-step PostgreSQL installation, user provisioning, and schema setup instructions.

---

## 🔄 Data Migration Guide (SQLite to PostgreSQL)

A dedicated, idempotent migration CLI tool is provided ([migrate_sqlite_to_postgres.py](file:///migrate_sqlite_to_postgres.py)):

```bash
# 1. Validate source rows with Dry Run mode
python migrate_sqlite_to_postgres.py --dry-run

# 2. Run live migration to PostgreSQL
python migrate_sqlite_to_postgres.py --postgres-url "postgresql://msedcl_user:password@localhost:5432/msedcl_qa"
```

A detailed reconciliation report is automatically generated at `migration_report.json`. Detailed steps and rollback instructions are in [MIGRATION_GUIDE.md](file:///MIGRATION_GUIDE.md).

---

## 👥 Role-Based Access Control (RBAC)

The system enforces strict server-side authorization:
- **Super Admin (`admin`)**: Complete access, user provisioning, role assignments, system settings, purge actions.
- **Operations Manager (`manager`)**: Access to audits, warnings, analytics, user deactivation, and filtered report exports.
- **Quality Auditor (`quality`)**: Call auditing, spreadsheet uploads, warning draft generation, advisor coaching.
- **Advisor / Viewer (`advisor` / `viewer`)**: Read-only access strictly bounded to their assigned `advisor_id`. Blocked from seeing other staff records or admin tools.

---

## 🚀 Quick Start & Installation

### 1. Clone & Set Up Environment
```bash
git clone https://github.com/WeWake2026/MSEDCL.git
cd MSEDCL

# Create virtual environment
python -m venv .venv
# Activate on Windows:
.\.venv\Scripts\activate
# Activate on Linux/Mac:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Set `DATABASE_URL` (if using PostgreSQL) and your preferred `APP_SECRET_KEY`.

### 3. Initialize & Seed Database
```bash
python init_db.py
```

### 4. Start Application
```bash
# Development mode
python app.py

# Or production mode (Windows batch file):
run.bat
```
Visit `http://localhost:5000` and log in with default credentials:
- **Username**: `qaadmin`
- **Password**: `Msedcl@123`

---

## 🧪 Automated Testing & Verification

Run the comprehensive unit test suite:
```bash
python -m unittest test_system.py
```
This automatically verifies:
- Health check endpoints (`/healthz`)
- Security headers (nosniff, SAMEORIGIN, XSS protection)
- User authentication and salted password hashes
- Advisor data isolation
- Protected route redirection
- Migration tool dry-run execution

---

## 📚 Documentation Index

- [DATABASE_SETUP.md](file:///DATABASE_SETUP.md) - Detailed PostgreSQL setup, permissions, Docker setup, and backups.
- [MIGRATION_GUIDE.md](file:///MIGRATION_GUIDE.md) - SQLite to PostgreSQL migration, reconciliation, and rollback.
- [SECURITY.md](file:///SECURITY.md) - Security controls, headers, authentication, and vulnerability disclosures.
- [.env.example](file:///.env.example) - Environment variables reference template.
