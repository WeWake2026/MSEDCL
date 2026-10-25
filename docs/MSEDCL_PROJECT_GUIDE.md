# ⚡ MSEDCL Call Center Quality Audit System — Architecture & Strategic Roadmap

---

## 1. Executive Summary & Project Purpose

The **MSEDCL Quality Audit System** is an enterprise-grade web application engineered for the **Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL / Mahavitaran)** call center operations.

Its core mission is to:
1. **Track and evaluate customer call interactions** across both **Inbound Customer Service** (billing, outage complaints, meter issues) and **Outbound Operations** (arrears reminders, feedback verification).
2. **Standardize quality assurance metrics** (Quality Scores, Fatal Violations, Error Categories).
3. **Automate formal governance & compliance** (automated generation of Appreciation Letters, Warning Letters Tiers 1–3, and Performance Improvement Plans).
4. **Protect customer privacy** by ensuring customer personal identifiable information (PII) is masked and restricted from front-line agents.
5. **Empower advisors with official tools**, including the live **FY 2026–27 Mahavitaran Electricity Bill Calculator** (Industrial LT V, Commercial LT II, and Residential LT I).

---

## 2. Complete Application Architecture

```mermaid
graph TD
    Client[Web Browser Client] -->|HTTP / HTTPS| FlaskApp[Flask Web Core (app.py)]
    
    subgraph Security & Access Layer
        Auth[Session Auth / Scrypt Passwords]
        RBAC[Role-Based Access Control (@roles)]
        CSRF[Data Scoping & PII Protection]
    end

    subgraph Service Layer
        DBPool[database.py: Dual-Engine Connection Pool]
        DBExplorer[db_explorer_service.py: Admin Schema Browser]
        ExportEngine[export_service.py: Formula-Injection-Safe CSV/Excel]
        NoticeEngine[Smart Notice & Tier Engine: /api/advisor/lookup]
    end

    subgraph Data Stores
        SQLite[(Local SQLite: quality_audit.db)]
        Postgres[(PostgreSQL: 127.0.0.1:5432)]
    end

    FlaskApp --> Auth
    FlaskApp --> RBAC
    FlaskApp --> CSRF
    
    FlaskApp --> DBPool
    FlaskApp --> DBExplorer
    FlaskApp --> ExportEngine
    FlaskApp --> NoticeEngine
    
    DBPool -->|DATABASE_URL defined| Postgres
    DBPool -->|Fallback / Default| SQLite
```

### Technology Stack:
* **Backend**: Python 3.12+ / Flask 3.0
* **Database Dual-Mode**: PostgreSQL (production pooling with statement timeouts) & SQLite (`quality_audit.db` local engine)
* **Data Processing**: Pandas & OpenPyXL for large spreadsheet parsing and data sanitization
* **Frontend**: Responsive HTML5, Vanilla CSS design system (`static/css/style.css`), Chart.js for data visualization, and Tailwind CSS component integration

---

## 3. User Roles & Permission Matrix (RBAC)

The system enforces strict data isolation so front-line advisors can only access their own performance data without viewing confidential customer details or other agents' data.

| Feature / URL Endpoint | Admin (`admin`) | Quality Auditor (`quality`) | Call Center Manager (`manager`) | Advisor / Viewer (`advisor`, `viewer`) |
| :--- | :---: | :---: | :---: | :---: |
| **Executive Dashboard** (`/`) | Full Call Center Metrics | QA Benchmarks & Trends | Operations & SLA KPI | Strictly Own Performance Metrics |
| **Inbound Audits** (`/audits/Inbound`) | All Audits + Observation Text | All Audits + Observation Text | All Audits + Observation Text | Own Calls Only (Customer Observation **Hidden**) |
| **Outbound Audits** (`/audits/Outbound`) | All Audits + Observation Text | All Audits + Observation Text | All Audits + Observation Text | Own Calls Only (Customer Observation **Hidden**) |
| **Fatal Infractions** (`/fatal-calls`) | Full Fatal Logs & Root Cause | Full Fatal Logs & Root Cause | Full Fatal Logs & Root Cause | Own Fatal Notices Only |
| **Score Reports** (`/score-reports`) | Company-wide Score Bands | Company-wide Score Bands | Company-wide Score Bands | Own Quality Score Progress |
| **Advisor 360 Search** (`/advisor-search`) | Full Lookup & Telemetry | Full Lookup & Telemetry | Full Lookup & Telemetry | Blocked (Restricted to Own Profile) |
| **Auditor Reports** (`/auditors`) | Evaluator Calibrations | Evaluator Calibrations | Evaluator Calibrations | Blocked |
| **Warnings & Notice Dispatch** (`/warnings`) | Create, Send & Close Notices | Create & Review Notices | Create, Review & Close | View Own Notice & Submit Action Plan |
| **Bill Calculator** (`/calculator`) | Allowed | Allowed | Allowed | Allowed (Open to All Staff) |
| **Import Files / Upload** (`/upload`) | Allowed | Allowed | Allowed | Blocked |
| **Export Center** (`/export-center`) | Unrestricted CSV & Excel | Department Exports | Department Exports | Own Records Export |
| **Database Explorer** (`/admin/database-explorer`) | Live SQL/Table Explorer | Blocked (403) | Blocked (403) | Blocked (403) |
| **User Management** (`/admin/users`) | Add/Edit/Reset Users | Blocked (403) | View/Manage Staff | Blocked (403) |
| **System Settings & Audit Logs** | Allowed | Blocked (403) | Blocked (403) | Blocked (403) |

---

## 4. Current Core Features Explained

### 1. Ingestion Engine (`/upload`)
* Uploads monthly or daily Inbound/Outbound audit workbooks (`.xlsx`, `.csv`).
* Auto-normalizes column headers (e.g., automatically maps `Area of Improvement` &rarr; `Error Categories`).
* Calculates unique SHA-256 row hashes to **prevent duplicate audits**.

### 2. High-Density Operational Grids (`/audits/Inbound`, `/audits/Outbound`)
* Compact multi-line cells (`Advisor Name bold` + `Employee ID pill`).
* Visual color indicators:
  * Green $\ge 90\%$ (Exceeds expectations)
  * Blue $80\% - 89\%$ (Acceptable standard)
  * Amber/Red $< 80\%$ (Low score)
  * Solid Red Badge for **FATAL** infractions.
* Integrated audio player widget for inline call stream listening.
* **Customer Data Privacy**: Automatic masking of customer telephone numbers, meter IDs, and observation paragraphs for front-line advisors.

### 3. Automated Notice & Tier Engine (`/warnings` & `/api/advisor/lookup`)
* Evaluates an advisor's complete history on demand:
  1. **Tier 1 (First Warning Letter)**: For first-time performance concerns ($<80\%$).
  2. **Tier 2 (Second Warning Letter)**: For repeated deviations.
  3. **Tier 3 (Final Warning Letter)**: Pre-PIP warning before HR escalation.
  4. **Tier 4 (Performance Improvement Plan - PIP)**: 30-day target timeline ($85\%$ benchmark target).
  5. **Tier 5 (Appreciation Letter)**: For exemplary service ($\ge 90\%$).
* Automatically extracts and injects call evidence, dates, scores, and specific coaching points.

### 4. Mahavitaran Electricity Bill Calculator (`/calculator`)
* Dual Industrial LT V and Residential LT I(B) calculator.
* Calculates exact energy slabs, fuel adjustment charges (FAC), wheeling charges, TOD tariffs, electricity duty ($7.5\%$ ind / $16\%$ res), and sale taxes ($28.94$ paise/unit).
* Starts clean with blank fields and 1-click **Reset Form** and **Print Sheet** capabilities.

### 5. Admin Database Explorer (`/admin/database-explorer`)
* Safe schema browser with live engine indicator (PostgreSQL vs. SQLite).
* Masked sensitive columns (passwords masked as `[REDACTED]`).
* Streaming CSV export protected against spreadsheet Formula Injection (`=`, `+`, `-`, `@`).

---

## 5. Recommended Improvements & Action Plan

To transition this application from a local management portal into an automated enterprise platform, the following improvements are recommended:

### 🚀 Priority 1: User Experience & Front-End Enhancements
1. **Interactive Call Audio Waveform Player**:
   * *Current*: Native browser `<audio>` tag.
   * *Improvement*: Replace with a mini WaveSurfer.js widget that visualizes speech waves, dead-air markers (highlighting pauses $>30$ seconds), and timestamp bookmarks.
2. **Real-Time Search with Debounced Auto-Filtering**:
   * *Current*: Page reloads on form submit.
   * *Improvement*: Use lightweight Fetch/AJAX to filter audits live without full page refresh.
3. **Dark / Light Theme Toggle**:
   * Add a topbar toggle that remembers user preference via `localStorage`.

### 🛡️ Priority 2: Security & Privacy Compliance
1. **Automated PII Sanitization via Regular Expressions**:
   * Add a database filter that masks consumer Aadhaar, 12-digit consumer account numbers (`1602******18`), and mobile numbers (`98****1234`) across all observation notes before storage.
2. **Audit Log Retention & Two-Factor Authentication (2FA)**:
   * Provide optional Time-Based One-Time Password (TOTP) 2FA for Admin and Manager accounts.
3. **Session Timeout Notification**:
   * Provide a modal warning after 15 minutes of inactivity before automatic logout.

### ⚙️ Priority 3: Automated Workflow & Notifications
1. **Background Email Dispatcher via Celery / Redis**:
   * *Current*: Warning emails are dispatched synchronously over SMTP.
   * *Improvement*: Queue email dispatches in the background with automatic retry on network failure.
2. **Advisor Dispute / Appeal Workflow**:
   * Allow advisors who disagree with a low audit score or fatal tag to click **"Request Score Review"**.
   * Routes the dispute to the Quality Lead for blind re-evaluation.
3. **Automated Weekly Quality Digest (PDF / Email)**:
   * Generate an automated PDF summary for Team Leaders showing top errors, fatal counts, and coaching progress.

### 🗄️ Priority 4: Database & Scalability
1. **Production PostgreSQL Switch**:
   * Migrate existing 1,251 records from `quality_audit.db` to local PostgreSQL using `migrate_sqlite_to_postgres.py` once `.env` credentials are confirmed.
2. **Database Table Partitioning**:
   * As audits exceed 100,000+ rows, partition the `audits` table by year and month for sub-millisecond query performance.

---

## 6. Project Directory Map

```text
MSEDCL_Quality_Audit_System/
├── app.py                      # Core Flask Application & API Routes
├── database.py                 # Dual-Engine Connection Pool (PostgreSQL / SQLite)
├── db_explorer_service.py      # Admin Database Explorer Backend
├── export_service.py           # Formula-Injection-Safe CSV/Excel Exporter
├── init_db.py                  # Database Schema Initializer & Demo Seeder
├── migrate_sqlite_to_postgres.py # SQLite to PostgreSQL Migration Script
├── test_system.py              # Automated Unit Test Suite (11 Tests)
├── quality_audit.db            # Live SQLite Database (1,251 Audits, 5 Users)
├── static/
│   ├── css/style.css           # MSEDCL Enterprise Design System & Styling
│   └── js/chart.umd.min.js     # Chart.js Library
└── templates/
    ├── layout.html             # Base Template (Navigation, Topbar, Alerts)
    ├── dashboard.html          # Executive & Advisor KPI Dashboard
    ├── audits_view.html        # Compact 8-Column Audits Data Grid
    ├── advisor_search.html     # Advisor 360 Performance Telemetry
    ├── warnings.html           # 5-Tier Formal Notices & Smart Auto-Fill
    ├── calculator.html         # FY 26-27 Mahavitaran Electricity Bill Calculator
    ├── db_explorer.html        # Admin Database Explorer UI
    ├── upload.html             # Ingestion & Workbook Ingestion Log
    ├── export_center.html      # Centralized Reporting & Export Hub
    └── user_management.html    # Role-Based User Administration
```
