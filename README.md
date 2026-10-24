<div align="center">

# ⚡ Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)
### Enterprise Quality Audit & Performance Analytics Management Portal

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Flask](https://img.shields.io/badge/Flask-3.0.3-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![SQLite](https://img.shields.io/badge/SQLite-3.0+-003B57?style=for-the-badge&logo=sqlite&logoColor=white)](https://sqlite.org)
[![Chart.js](https://img.shields.io/badge/Chart.js-4.4.1-FF6384?style=for-the-badge&logo=chartdotjs&logoColor=white)](https://chartjs.org)
[![Render](https://img.shields.io/badge/Render-Deploy%20Ready-46E3B7?style=for-the-badge&logo=render&logoColor=white)](https://render.com)
[![Status](https://img.shields.io/badge/Quality%20System-Active%20%26%20Protected-success?style=for-the-badge)]()

<br/>

<img src="https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/Python-Dark.svg" width="48" height="48" alt="Python" />
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/Flask-Dark.svg" width="48" height="48" alt="Flask" />
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/HTML.svg" width="48" height="48" alt="HTML" />
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/CSS.svg" width="48" height="48" alt="CSS" />
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/Javascript.svg" width="48" height="48" alt="JS" />
&nbsp;&nbsp;&nbsp;&nbsp;
<img src="https://raw.githubusercontent.com/tandpfun/skill-icons/main/icons/SQLite.svg" width="48" height="48" alt="SQLite" />

<br/>

**A mission-critical Quality Assurance, Contact Center Analytics, Disciplinary Warning/PIP Governance, and Multi-department Audit portal engineered for MSEDCL.**

---

</div>

## 📑 Table of Contents
- [✨ Core Highlights & Features](#-core-highlights--features)
- [📊 Executive Analytics & Visualizations](#-executive-analytics--visualizations)
- [🛡️ Warning & PIP Action Governance](#-warning--pip-action-governance)
- [👥 Role-Based Access Control (RBAC)](#-role-based-access-control-rbac)
- [🚀 Quick Start & Installation (Windows / Linux)](#-quick-start--installation)
- [🌐 Cloud Deployment (Render / Gunicorn)](#-cloud-deployment-render--gunicorn)
- [📧 Email Dispatch Configuration](#-email-dispatch-configuration)
- [📥 Automated Report Generation & Exports](#-automated-report-generation--exports)
- [🔐 Security Best Practices](#-security-best-practices)

---

## ✨ Core Highlights & Features

| Capability | Description |
| :--- | :--- |
| **⚡ Live Ingestion & Auditing** | Seamless `.xlsx`, `.xlsm`, and `.csv` audit file ingestion with duplicate hashing & validation. |
| **📅 Real-Time Live Clock & Sync** | Auto-ticking client banner with transmission tower electrical substation art and live sync state. |
| **🏆 360° Advisor Drilldown** | Instant employee history search, coaching flags, fatal alerts, and custom PDF/Excel summaries. |
| **🛡️ Disciplinary Warning & PIPs** | Multi-tier corrective notice generator with draft email transmission and admin deletion control. |
| **🌓 Day / Night Smart Themes** | Dynamic dark/light mode toggle with persistent local preferences and contrast compliance. |
| **📱 100% Mobile & Tablet Responsive** | Modern layout with collapsible sidebar, floating quick-links, and responsive analytics tables. |

---

## 📊 Executive Analytics & Visualizations

The Home Dashboard integrates 6 interactive Chart.js visualizations that auto-adapt to day/night palettes:

```
+---------------------------------------------------------------------------------------+
|                                    MSEDCL QA KPI ROW                                  |
| [ TOTAL AUDITS ]   [ INBOUND ]   [ OUTBOUND ]   [ FATAL CALLS ]   [ AVG SCORE % ]     |
+---------------------------------------------------------------------------------------+
|                                  PRIMARY ANALYTICS ROW                                |
|  [ Score Distribution ]       [ Fatal Ratio Donut ]      [ Inbound vs Outbound Trend] |
|   • Good (>90%)                • Fatal Calls              • Daily Call Volumes        |
|   • Average (80-90%)           • Non-Fatal Calls          • Comparative Trajectory    |
|   • Low (<80%)                                                                        |
+---------------------------------------------------------------------------------------+
|                                 SECONDARY ANALYTICS ROW                               |
|  [ Auditor Activity Bar ]     [ Top Advisor Scores ]     [ Warning & PIP Status ]     |
|   • Evaluations Per QA         • Ranked High Performers   • Open / In Progress        |
|   • Activity Benchmarks        • Score Percentages        • Closed & Active PIPs      |
+---------------------------------------------------------------------------------------+
|                     ADVISOR-WISE QUALITY SUMMARY REGISTER (FILTERABLE)                |
+---------------------------------------------------------------------------------------+
```

---

## 🛡️ Warning & PIP Action Governance

Formal corrective action governance system:
1. **Notice Creation**: Automated draft generation with interaction ID, evaluator remarks, and targeted review dates.
2. **Action Register**:
   - `✉️ Send`: Direct SMTP transmission with full audit trail.
   - `👁️ View`: Expandable drawer showing detailed notice body and advisor response.
   - `💬 Advisor Response`: Direct input form for advisors to submit corrective action plans.
   - `🗑️ Remove`: **Admin-only deletion button** allowing permanent removal of records with confirmation.
3. **Resolution Lifecycle**: *Open* ➔ *In Progress* ➔ *Closed / Resolved*.

---

## 👥 Role-Based Access Control (RBAC)

| Role | Dashboard | Ingestion | Reports & Exports | Issue PIP | Delete PIP | User Management |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Admin** | ✅ Full | ✅ Yes | ✅ All 12 Formats | ✅ Yes | ✅ **Yes** | ✅ Complete |
| **Quality** | ✅ Full | ✅ Yes | ✅ All 12 Formats | ✅ Yes | ❌ No | ❌ View Only |
| **Manager** | ✅ Full | ❌ Read | ✅ View & Export | ✅ Yes | ❌ No | ❌ View Only |
| **Advisor** | 🔒 Own Data | ❌ No | 🔒 Own Performance| 💬 Feedback Only | ❌ No | ❌ No |

---

## 🚀 Quick Start & Installation

### 1. Clone & Set Up Directory
```bash
git clone https://github.com/WeWake2026/MSEDCL.git
cd MSEDCL
```

### 2. Set Up Virtual Environment

**Windows PowerShell:**
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**Linux / macOS:**
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Initialize Database & Migrations
```bash
python migrate_db.py
python init_db.py
```

### 4. Run the Application
```bash
python app.py
```
Visit **`http://localhost:5000`** in your browser. Default Admin: `admin` / `admin123` (or configured via `.env`).

---

## 🌐 Cloud Deployment (Render / Gunicorn)

This repository is optimized for one-click deployment on **Render**:

- **Build Command:**
  ```bash
  pip install -r requirements.txt
  ```
- **Start Command:**
  ```bash
  gunicorn app:app --workers 2 --bind 0.0.0.0:$PORT
  ```
- **Environment Variables Required:**
  - `APP_SECRET_KEY`: Long cryptographic string.
  - `DATABASE_PATH`: `quality_audit.db`
  - `MAX_UPLOAD_MB`: `25`

---

## 📧 Email Dispatch Configuration

Configure SMTP in `.env` or through **Admin Settings** (`/admin/settings`):

```ini
SMTP_HOST=smtp.office365.com
SMTP_PORT=587
SMTP_USERNAME=quality.audit@msedcl.in
SMTP_PASSWORD=YourAppPasswordHere
SMTP_FROM=quality.audit@msedcl.in
```

---

## 📥 Automated Report Generation & Exports

- **Individual Category Reports**: Excel (`.xlsx`) & CSV formats with auto-fitted columns, brand borders, and frozen title headers.
- **Combined Master Workbook**: Multi-sheet workbook assembling Inbound, Outbound, Fatal, and Advisor performance summaries.
- **Bulk ZIP Archive**: One-click download of all 12 operational datasets.

---

## 🔐 Security Best Practices

- **Zero Plain-Text Credentials**: Passwords hashed using PBKDF2 with SHA-256 and unique salts.
- **Strict CSRF & Session Isolation**: Session cookies marked `HttpOnly` and `SameSite=Lax`.
- **SQL Parameterization**: Zero raw query string interpolation to prevent injection attacks.
- **Immutable System Audit Log**: Dedicated security audit records for all user logins, failed attempts, and administrative actions.

---

<div align="center">
<sub>Designed & Maintained for Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL). All rights reserved.</sub>
</div>
