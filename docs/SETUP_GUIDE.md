# Windows Setup & Operations Guide (Command Prompt / CMD)
## MSEDCL Quality Audit System

This guide outlines exact CMD / PowerShell commands to set up, operate, test, migrate, and maintain the MSEDCL Quality Audit application on Windows with Python and PostgreSQL.

---

### Step 1: Open Project Directory in CMD

Open **Command Prompt (CMD)** as Administrator or standard user:
```cmd
cd /d "c:\Users\Vivek\Downloads\MSEDCL_Quality_Audit_System_Starter\MSEDCL_Quality_Audit_System"
```

---

### Step 2: Create and Activate Python Virtual Environment

```cmd
:: Create virtual environment (if not already existing)
python -m venv .venv

:: Activate virtual environment in Command Prompt (CMD)
.venv\Scripts\activate.bat

:: Or if using PowerShell:
:: .venv\Scripts\Activate.ps1
```

---

### Step 3: Install Required Dependencies

```cmd
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\pip.exe install -r requirements.txt
```

---

### Step 4: Verify PostgreSQL Service on Windows

Verify the PostgreSQL Windows service is running:
```cmd
powershell -Command "Get-Service *postgres*"
```
If the service is stopped, start it:
```cmd
net start postgresql-x64-18
```

---

### Step 5: Connect Using `psql` and Create Application Role & Database

Open `psql` as the PostgreSQL administrator:
```cmd
"C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h 127.0.0.1
```
*(Enter your PostgreSQL master password when prompted)*

Execute the least-privilege setup commands in the `psql` console:
```sql
-- 1. Create dedicated application user
CREATE USER msedcl_user WITH ENCRYPTED PASSWORD 'MsedclSecure2026!';

-- 2. Create the QA database owned by msedcl_user
CREATE DATABASE msedcl_qa OWNER msedcl_user;

-- 3. Connect to msedcl_qa
\c msedcl_qa

-- 4. Grant schema permissions
GRANT ALL ON SCHEMA public TO msedcl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO msedcl_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO msedcl_user;

-- 5. Exit psql
\q
```

---

### Step 6: Configure Environment (`.env`)

Edit your `.env` file or configure it using the template:
```cmd
copy .env.example .env
```

Set the database connection parameters in `.env`:
```ini
# Option A: Full Connection URI (URL-encode reserved characters such as @ or %)
DATABASE_URL=postgresql://qa_app:YOUR_URL_ENCODED_PASSWORD@127.0.0.1:5432/qa_dashboard

# Or Option B: Discrete Parameters
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=qa_dashboard
DB_USER=qa_app
DB_PASSWORD=YOUR_PASSWORD
DB_TIMEOUT=10
```

---

### Step 7: Initialize or Migrate Schema to PostgreSQL

Initialize tables and seed administrative users in PostgreSQL:
```cmd
.venv\Scripts\python.exe init_db.py
```

If you have existing SQLite data to migrate to PostgreSQL:
```cmd
:: 1. Dry run reconciliation (safe test)
.venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py --dry-run

:: 2. Execute migration with full row verification
.venv\Scripts\python.exe scripts\migrate_sqlite_to_postgres.py
```

---

### Step 8: Run Automated Tests

Execute the automated test suite to verify database connection, role security, user-wise data isolation, row reconciliation, and Database Explorer functionality:
```cmd
.venv\Scripts\python.exe test_system.py
```

---

### Step 9: Start the Flask Application

```cmd
.venv\Scripts\python.exe app.py
```
Expected output:
```
Database initialized successfully.
 * Serving Flask app 'app'
 * Running on http://127.0.0.1:5000
```

---

### Step 10: Access Application & Admin Database Explorer

1. Open your browser and navigate to: `http://127.0.0.1:5000`
2. Log in using the admin account:
   - **Username**: `qaadmin`
   - **Password**: `Msedcl@123`
3. Click on **Database Explorer** (`/admin/database-explorer`) in the left navigation sidebar.
4. Select any table (e.g., `audits`, `users`, `warnings`) to:
   - View metadata (columns, constraints, datatypes).
   - Browse paginated rows with sorting and real-time search.
   - Verify sensitive columns (`password_hash`) are redacted.
   - Export filtered table records safely to CSV.

---

### Step 11: Database Backup and Recovery

#### PostgreSQL Backup (pg_dump):
```cmd
"C:\Program Files\PostgreSQL\18\bin\pg_dump.exe" -U msedcl_user -h 127.0.0.1 -d msedcl_qa -F c -b -v -f "backup_msedcl_qa_%date:~-4,4%%date:~-7,2%%date:~-10,2%.dump"
```

#### PostgreSQL Restore (pg_restore):
```cmd
"C:\Program Files\PostgreSQL\18\bin\pg_restore.exe" -U msedcl_user -h 127.0.0.1 -d msedcl_qa -v "backup_msedcl_qa_file.dump"
```

#### SQLite Backup:
You can also download an SQLite backup directly from the admin dashboard at `/admin/backup/download` or copy `quality_audit.db`.
