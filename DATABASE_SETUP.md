# PostgreSQL Database Setup Guide

This guide details how to install, configure, secure, and operate PostgreSQL for the **MSEDCL Quality Audit System**.

---

## 1. Prerequisites & Version Requirements

- **PostgreSQL**: Version 14, 15, or 16+
- **Python**: 3.10, 3.11, or 3.12+
- **Driver**: `psycopg2-binary>=2.9.9` (already included in `requirements.txt`)

---

## 2. PostgreSQL Installation

### Windows (Local)
1. Download the installer from [EnterpriseDB PostgreSQL Downloads](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads).
2. Follow the setup wizard and note the master password for the `postgres` user.
3. Add PostgreSQL bin folder to PATH: `C:\Program Files\PostgreSQL\<version>\bin`.

### Linux (Ubuntu / Debian)
```bash
sudo apt update
sudo apt install -y postgresql postgresql-contrib libpq-dev
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### Docker (Recommended for Local Dev)
Run a self-contained PostgreSQL instance in one command:
```bash
docker run --name msedcl-postgres \
  -e POSTGRES_DB=msedcl_qa \
  -e POSTGRES_USER=msedcl_user \
  -e POSTGRES_PASSWORD=SecurePassword123! \
  -p 5432:5432 \
  -d postgres:16-alpine
```

---

## 3. Database & Least-Privilege User Creation

Log in as the postgres superuser:
```bash
# On Linux / Mac
sudo -u postgres psql

# On Windows
psql -U postgres
```

Execute the least-privilege setup commands:
```sql
-- 1. Create dedicated database user
CREATE USER msedcl_user WITH ENCRYPTED PASSWORD 'ReplaceWithStrongPassword!';

-- 2. Create application database
CREATE DATABASE msedcl_qa OWNER msedcl_user;

-- 3. Grant schema permissions
\c msedcl_qa
GRANT ALL PRIVILEGES ON DATABASE msedcl_qa TO msedcl_user;
GRANT ALL ON SCHEMA public TO msedcl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO msedcl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO msedcl_user;
```

---

## 4. Applying Schema & Initializing Database

### Automatic Initialization
When `DATABASE_URL` is set in `.env`, running the application or `python init_db.py` automatically initializes all tables, sequences, indexes, and initial seeds.

```bash
python init_db.py
```

### Manual Schema Execution
If you prefer manual migration via CLI:
```bash
psql -h localhost -U msedcl_user -d msedcl_qa -f schema.sql
```

---

## 5. Connecting Application via Environment Variables

Add your connection URI to `.env`:
```env
DATABASE_URL=postgresql://msedcl_user:ReplaceWithStrongPassword!@localhost:5432/msedcl_qa
DB_POOL_MIN=1
DB_POOL_MAX=10
```

*Note: If `DATABASE_URL` is omitted, the application automatically falls back to the embedded SQLite database (`quality_audit.db`).*

---

## 6. Verification and Health Check

Start the application and verify database connectivity:
```bash
curl http://127.0.0.1:5000/healthz
```
Expected response:
```json
{
  "healthy": true,
  "engine": "PostgreSQL",
  "error": null
}
```

---

## 7. Automated Backups & Restoration

### Backup Database (Logical Dump)
```bash
pg_dump -h localhost -U msedcl_user -d msedcl_qa -F c -b -v -f "msedcl_qa_backup_$(date +%Y%m%d_%H%M%S).dump"
```

### Restore Database
```bash
pg_restore -h localhost -U msedcl_user -d msedcl_qa -v "msedcl_qa_backup_20261025.dump"
```
