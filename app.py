import os, io, sqlite3, hashlib, secrets, smtplib, ssl, json, sys, time, threading, subprocess
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from functools import wraps

import pandas as pd
from flask import Flask, request, redirect, url_for, render_template, session, flash, send_file, abort
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import export_service

# Load environment configuration
env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

APP_SECRET = os.environ.get("APP_SECRET_KEY")
if not APP_SECRET:
    APP_SECRET = secrets.token_hex(32)
    os.environ["APP_SECRET_KEY"] = APP_SECRET

DB_PATH = os.environ.get("DATABASE_PATH", "quality_audit.db")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "25"))

# Auto-initialize database schema if database or tables don't exist yet
try:
    from init_db import init_db
    init_db(DB_PATH)
except Exception as _e:
    print(f"Warning initializing database: {_e}")

app = Flask(__name__)
app.secret_key = APP_SECRET
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = False  # Set to True automatically if behind HTTPS
ALLOWED_EXTENSIONS = {".xlsx", ".xlsm", ".csv"}

@app.after_request
def apply_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# --- Database helpers ---
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def log_audit(action, target="", details=""):
    try:
        actor = getattr(request, "current_user", None)
        username = actor["username"] if actor else "anonymous"
        role = actor["role"] if actor else "none"
        ip = request.remote_addr or "127.0.0.1"
        with db() as con:
            con.execute("""
                INSERT INTO audit_logs (timestamp, actor_username, actor_role, action, target_entity, details, ip_address)
                VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)
            """, (username, role, action, target, details, ip))
    except Exception:
        pass

def get_setting(key, default=""):
    try:
        with db() as con:
            r = con.execute("SELECT setting_value FROM system_settings WHERE setting_key=?", (key,)).fetchone()
            return r["setting_value"] if r else default
    except Exception:
        return default

# --- Authentication & Authorization ---
def login_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE id=? AND active=1", (session["user_id"],)).fetchone()
        if not user:
            session.clear()
            return redirect(url_for("login"))
        request.current_user = user
        return fn(*args, **kwargs)
    return wrapped

def roles(*allowed):
    def decorator(fn):
        @wraps(fn)
        @login_required
        def wrapped(*args, **kwargs):
            if request.current_user["role"] not in allowed:
                abort(403)
            return fn(*args, **kwargs)
        return wrapped
    return decorator

# --- Context Processor for Global Template Context ---
@app.context_processor
def inject_global_data():
    return {
        "user": getattr(request, "current_user", None),
        "app_name": get_setting("company_name", "MSEDCL Quality Audit"),
        "import_enabled": get_setting("enable_excel_import", "true") == "true",
        "max": max,
        "min": min
    }

# --- Column Detection & Cleaning ---
ALIASES = {
    "call_id": ["call id", "callid", "interaction id", "recording id", "unique call id", "call reference"],
    "call_date": ["call date", "date of call", "interaction date", "date"],
    "evaluation_date": ["evaluation date", "audit date", "evaluated date", "quality evaluation date"],
    "advisor_id": ["employee id", "advisor id", "agent id", "employee code", "advisor code", "emp id"],
    "advisor_name": ["employee name", "advisor name", "agent name", "name of advisor", "employee"],
    "team_leader": ["team leader name", "team leader", "tl name", "tl"],
    "auditor_name": ["evaluator name", "auditor name", "quality auditor", "evaluator"],
    "quality_score": ["quality score", "score", "qa score", "quality %", "quality percentage", "final score"],
    "is_fatal": ["fatal", "fatal call", "fatal status", "is fatal", "fatal error"],
    "fatal_category": ["fatal category", "fatal reason", "fatal error category", "fatal error"],
    "error_categories": ["error categories", "error category", "error type", "major error", "error"],
    "observation": ["call observation", "observation", "remarks", "auditor remarks", "feedback"],
    "location": ["location", "city", "center", "site"],
    "audio_url": ["call audio url", "audio url", "recording url", "call recording", "call audio", "audio link", "recording link", "audio"]
}

def norm(s):
    return " ".join(str(s).strip().lower().replace("\n", " ").replace("_", " ").split())

def detect_columns(columns):
    normalized = {norm(c): c for c in columns}
    mapping = {}
    for field, aliases in ALIASES.items():
        for alias in aliases:
            if norm(alias) in normalized:
                mapping[field] = normalized[norm(alias)]
                break
    return mapping

def parse_score(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        v = float(str(value).replace("%", "").strip())
        if 0 <= v <= 1:
            v *= 100
        if 0 <= v <= 100:
            return round(v, 2)
    except (ValueError, TypeError):
        pass
    return None

def truthy_fatal(value):
    s = norm(value)
    return s in {"yes", "y", "true", "1", "fatal", "fatal call", "critical"} or ("fatal" in s and s not in {"non fatal", "not fatal"})

def parse_date(value):
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        dt = pd.to_datetime(value, errors="coerce", dayfirst=True)
        if pd.isna(dt): return None
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None

def read_upload(file_storage):
    filename = secure_filename(file_storage.filename or "upload.xlsx")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload .xlsx, .xlsm or .csv files only.")
    raw = file_storage.read()
    if ext == ".csv":
        df = pd.read_csv(io.BytesIO(raw), dtype=object)
    else:
        df = pd.read_excel(io.BytesIO(raw), dtype=object)
    df = df.dropna(how="all")
    if df.empty:
        raise ValueError("The uploaded file has no data rows.")
    return filename, df

def make_hash(dept, row):
    key = "|".join(str(row.get(k) or "").strip().lower() for k in
                   ["call_id", "call_date", "evaluation_date", "advisor_id", "advisor_name", "quality_score", "is_fatal"])
    return hashlib.sha256((dept.lower() + "|" + key).encode("utf-8")).hexdigest()

def import_dataframe(df, department, filename, username):
    mapping = detect_columns(df.columns)
    if "advisor_name" not in mapping:
        raise ValueError("Advisor Name column was not detected. Please verify your column headers.")
    if "quality_score" not in mapping:
        raise ValueError("Quality Score column was not detected. Please ensure a Score header exists.")
    
    batch_id = secrets.token_hex(8)
    seen, inserted, duplicates, rejected = len(df), 0, 0, 0
    errors = []
    now = datetime.now().isoformat(timespec="seconds")
    
    with db() as con:
        for ix, source_row in df.iterrows():
            row = {}
            for field, source_col in mapping.items():
                val = source_row.get(source_col)
                row[field] = None if pd.isna(val) else val
            advisor = str(row.get("advisor_name") or "").strip()
            score = parse_score(row.get("quality_score"))
            if not advisor or score is None:
                rejected += 1
                errors.append({"row": int(ix)+2, "reason": "Missing advisor name or invalid quality score"})
                continue
                
            fatal_raw = str(row.get("is_fatal") or "")
            fatal_category = str(row.get("fatal_category") or "").strip()
            error_categories = str(row.get("error_categories") or "").strip()
            fatal = int(truthy_fatal(fatal_raw) or bool(fatal_category and "non fatal" not in norm(fatal_category)))
            if fatal and not fatal_category:
                fatal_category = "Fatal — category needs review"
                
            record = {
                "call_id": str(row.get("call_id") or "").strip() or None,
                "call_date": parse_date(row.get("call_date")),
                "evaluation_date": parse_date(row.get("evaluation_date")),
                "advisor_id": str(row.get("advisor_id") or "").strip() or None,
                "advisor_name": advisor,
                "team_leader": str(row.get("team_leader") or "").strip() or None,
                "auditor_name": str(row.get("auditor_name") or "").strip() or None,
                "quality_score": score,
                "is_fatal": fatal,
                "fatal_category": fatal_category or None,
                "error_categories": error_categories or None,
                "observation": str(row.get("observation") or "").strip() or None,
                "location": str(row.get("location") or "").strip() or None,
                "audio_url": str(row.get("audio_url") or "").strip() or None
            }
            row_hash = make_hash(department, record)
            try:
                con.execute("""INSERT INTO audits(department,call_id,call_date,evaluation_date,advisor_id,advisor_name,
                team_leader,auditor_name,quality_score,is_fatal,fatal_category,error_categories,observation,location,
                audio_url,source_file,import_batch,row_hash,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (department, record["call_id"], record["call_date"], record["evaluation_date"], record["advisor_id"],
                 record["advisor_name"], record["team_leader"], record["auditor_name"], record["quality_score"],
                 record["is_fatal"], record["fatal_category"], record["error_categories"], record["observation"],
                 record["location"], record["audio_url"], filename, batch_id, row_hash, now))
                inserted += 1
            except sqlite3.IntegrityError:
                duplicates += 1
                
        report = {"errors": errors[:200], "mapping": mapping}
        con.execute("""INSERT INTO import_batches(id,department,source_file,uploaded_by,uploaded_at,rows_seen,
        rows_inserted,duplicates,rejected,report_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (batch_id, department, filename, username, now, seen, inserted, duplicates, rejected, json.dumps(report, default=str)))
        
    log_audit("IMPORT_EXCEL", filename, f"Dept: {department}, Inserted: {inserted}, Dups: {duplicates}, Rejected: {rejected}")
    return {"batch_id": batch_id, "seen": seen, "inserted": inserted, "duplicates": duplicates, "rejected": rejected, "mapping": mapping, "errors": errors}

# --- Routes: Auth ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        with db() as con:
            user = con.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            with db() as con:
                con.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
            log_audit("LOGIN_SUCCESS", username, "User logged in")
            return redirect(url_for("dashboard"))
        log_audit("LOGIN_FAILED", username, "Invalid login credentials")
        flash("Invalid username or password.", "error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    log_audit("LOGOUT", session.get("user_id", ""), "User signed out")
    session.clear()
    return redirect(url_for("login"))

# --- Routes: Dashboard ---
@app.route("/")
@login_required
def dashboard():
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    selected_auditor = request.args.get("auditor", "").strip()
    advisor_query = request.args.get("advisor_q", "").strip()
    score_bracket = request.args.get("score_bracket", "All")
    fatal_status = request.args.get("fatal_status", "All")
    
    where = ["1=1"]
    params = []
    if dept in ("Inbound", "Outbound"):
        where.append("department=?")
        params.append(dept)
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)
    if selected_auditor:
        where.append("auditor_name=?")
        params.append(selected_auditor)
    if advisor_query:
        where.append("(advisor_id LIKE ? OR advisor_name LIKE ?)")
        params.extend([f"%{advisor_query}%", f"%{advisor_query}%"])
    if fatal_status == "fatal":
        where.append("is_fatal=1")
    elif fatal_status == "non_fatal":
        where.append("is_fatal=0")
    if score_bracket == "low":
        where.append("quality_score < 80")
    elif score_bracket == "mid":
        where.append("quality_score >= 80 AND quality_score <= 90")
    elif score_bracket == "good":
        where.append("quality_score > 90")
    
    # Restrict viewer with advisor_id to their own data only
    curr_user = getattr(request, "current_user", None)
    if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
        where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
        
    sql_base = " FROM audits WHERE " + " AND ".join(where)
    
    with db() as con:
        # System live status metadata
        last_batch = con.execute("SELECT uploaded_at, source_file FROM import_batches ORDER BY uploaded_at DESC LIMIT 1").fetchone()
        last_sync_time = last_batch["uploaded_at"] if last_batch else None
        last_sync_file = last_batch["source_file"] if last_batch else None
        all_time_records = con.execute("SELECT COUNT(*) FROM audits").fetchone()[0]

        total_audits = con.execute("SELECT COUNT(*)" + sql_base, params).fetchone()[0]
        inbound_count = con.execute("SELECT COUNT(*)" + sql_base + " AND department='Inbound'", params).fetchone()[0]
        outbound_count = con.execute("SELECT COUNT(*)" + sql_base + " AND department='Outbound'", params).fetchone()[0]
        fatal_count = con.execute("SELECT COUNT(*)" + sql_base + " AND is_fatal=1", params).fetchone()[0]
        low_score_count = con.execute("SELECT COUNT(*)" + sql_base + " AND quality_score < 80", params).fetchone()[0]
        avg_score_count = con.execute("SELECT COUNT(*)" + sql_base + " AND quality_score >= 80 AND quality_score <= 90", params).fetchone()[0]
        good_score_count = con.execute("SELECT COUNT(*)" + sql_base + " AND quality_score > 90", params).fetchone()[0]
        
        avg_row = con.execute("SELECT ROUND(AVG(quality_score), 2) " + sql_base, params).fetchone()
        overall_avg = avg_row[0] if avg_row and avg_row[0] is not None else 0.0
        fatal_rate = (fatal_count / max(1, total_audits)) * 100
        
        # Warnings & PIP counts (filtered if viewer)
        warn_where = ["1=1"]
        warn_params = []
        if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
            warn_where.append("(advisor_id=? OR advisor_name LIKE ?)")
            warn_params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
        warn_base = " FROM warnings WHERE " + " AND ".join(warn_where)

        total_warnings = con.execute("SELECT COUNT(*)" + warn_base, warn_params).fetchone()[0]
        open_warnings = con.execute("SELECT COUNT(*)" + warn_base + " AND status='Open'", warn_params).fetchone()[0]
        in_progress_warnings = con.execute("SELECT COUNT(*)" + warn_base + " AND status='In Progress'", warn_params).fetchone()[0]
        closed_warnings = con.execute("SELECT COUNT(*)" + warn_base + " AND status='Closed'", warn_params).fetchone()[0]
        active_pips = con.execute("SELECT COUNT(*)" + warn_base + " AND warning_type LIKE '%PIP%' AND status != 'Closed'", warn_params).fetchone()[0]

        # Auditor list
        auditors_list = [r[0] for r in con.execute("SELECT DISTINCT auditor_name FROM audits WHERE auditor_name IS NOT NULL AND auditor_name != '' ORDER BY auditor_name").fetchall()]

        # Auditor activity
        auditor_stats = con.execute("""
            SELECT auditor_name, COUNT(*) as cnt """ + sql_base + """ AND auditor_name IS NOT NULL
            GROUP BY auditor_name ORDER BY cnt DESC LIMIT 10
        """, params).fetchall()

        # Date timeline
        date_stats = con.execute("""
            SELECT COALESCE(call_date, evaluation_date) as dt, COUNT(*) as cnt """ + sql_base + """
            AND COALESCE(call_date, evaluation_date) IS NOT NULL
            GROUP BY dt ORDER BY dt DESC LIMIT 14
        """, params).fetchall()
        date_stats = list(reversed(date_stats))

        # Advisor Summary Table
        advisor_summary = con.execute("""
            SELECT advisor_id, advisor_name, department, COUNT(*) as calls,
                   ROUND(AVG(quality_score), 2) as avg_score,
                   SUM(is_fatal) as fatals,
                   SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_calls,
                   SUM(CASE WHEN quality_score >= 80 AND quality_score <= 90 THEN 1 ELSE 0 END) as mid_calls,
                   SUM(CASE WHEN quality_score > 90 THEN 1 ELSE 0 END) as good_calls
            """ + sql_base + """
            GROUP BY COALESCE(advisor_id, advisor_name), department
            ORDER BY fatals DESC, low_calls DESC LIMIT 20
        """, params).fetchall()

        # Top Advisors for Chart (Ranked by quality score)
        top_advisors_query = con.execute("""
            SELECT advisor_name, ROUND(AVG(quality_score), 2) as avg_score, COUNT(*) as cnt
            """ + sql_base + """
            GROUP BY COALESCE(advisor_id, advisor_name)
            ORDER BY avg_score DESC, cnt DESC LIMIT 8
        """, params).fetchall()

    metrics = {
        "total_audits": total_audits,
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
        "fatal_count": fatal_count,
        "fatal_rate": fatal_rate,
        "low_score_count": low_score_count,
        "avg_score_count": avg_score_count,
        "good_score_count": good_score_count,
        "overall_avg": overall_avg,
        "total_warnings": total_warnings,
        "open_warnings": open_warnings,
        "active_pips": active_pips
    }

    chart_data = {
        "low_scores": low_score_count,
        "mid_scores": avg_score_count,
        "good_scores": good_score_count,
        "fatal_count": fatal_count,
        "non_fatal_count": max(0, total_audits - fatal_count),
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
        "dates": [r["dt"] for r in date_stats],
        "date_counts": [r["cnt"] for r in date_stats],
        "auditor_names": [r["auditor_name"] for r in auditor_stats],
        "auditor_counts": [r["cnt"] for r in auditor_stats],
        "top_advisors": [r["advisor_name"] for r in top_advisors_query],
        "top_scores": [r["avg_score"] for r in top_advisors_query],
        "open_warnings": open_warnings,
        "in_progress_warnings": in_progress_warnings,
        "closed_warnings": closed_warnings,
        "active_pips": active_pips
    }

    now_dt = datetime.now()
    if last_sync_time and len(last_sync_time) >= 10:
        try:
            # Parse ISO or SQLite datetime string
            clean_time_str = last_sync_time.replace("T", " ")[:19]
            parsed_dt = datetime.fromisoformat(clean_time_str)
            display_date = parsed_dt.strftime("%d %b %Y")
            display_time = parsed_dt.strftime("%I:%M %p")
        except Exception:
            display_date = now_dt.strftime("%d %b %Y")
            display_time = now_dt.strftime("%I:%M %p")
    else:
        # Default to real-time current date & clock
        display_date = now_dt.strftime("%d %b %Y")
        display_time = now_dt.strftime("%I:%M %p")

    sync_status = {
        "display_date": display_date,
        "display_time": display_time,
        "last_sync_time": last_sync_time or "Live Database",
        "last_sync_file": last_sync_file or "Connected",
        "all_time_records": all_time_records,
        "filtered_count": total_audits,
        "is_filtered": bool(dept != "All" or start or end or selected_auditor or advisor_query or score_bracket != "All" or fatal_status != "All")
    }

    return render_template("dashboard.html",
                           page="dashboard",
                           metrics=metrics,
                           chart_data=chart_data,
                           advisor_summary=advisor_summary,
                           auditors_list=auditors_list,
                           selected_auditor=selected_auditor,
                           advisor_query=advisor_query,
                           score_bracket=score_bracket,
                           fatal_status=fatal_status,
                           sync_status=sync_status,
                           dept=dept, start=start, end=end)

# --- Routes: Audits Views (Inbound, Outbound, Fatal, Scores) ---
@app.route("/audits")
@app.route("/audits/<dept>")
@login_required
def audits_view(dept="Inbound"):
    if dept not in ("Inbound", "Outbound"):
        dept = "Inbound"
    q = request.args.get("q", "").strip()
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    
    where = ["department=?"]
    params = [dept]
    curr_user = getattr(request, "current_user", None)
    if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
        where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
    if q:
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR call_id LIKE ? OR fatal_category LIKE ? OR error_categories LIKE ?)")
        params += [f"%{q}%"] * 5
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)

    sql = "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC LIMIT 500"
    with db() as con:
        rows = con.execute(sql, params).fetchall()

    return render_template("audits_view.html",
                           page=f"{dept.lower()}_audits",
                           title=f"{dept} Call Audits",
                           subtitle=f"Displaying evaluated customer calls for {dept} operations",
                           fixed_dept=True,
                           rows=rows, q=q, dept=dept, start=start, end=end)

@app.route("/fatal-calls")
@login_required
def fatal_calls():
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    
    where = ["is_fatal=1"]
    params = []
    curr_user = getattr(request, "current_user", None)
    if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
        where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
    if dept in ("Inbound", "Outbound"):
        where.append("department=?")
        params.append(dept)
    if q:
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR call_id LIKE ? OR fatal_category LIKE ?)")
        params += [f"%{q}%"] * 4
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)

    sql = "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC LIMIT 500"
    with db() as con:
        rows = con.execute(sql, params).fetchall()

    return render_template("audits_view.html",
                           page="fatal_calls",
                           title="Fatal Call Register",
                           subtitle="Critical compliance, zero-tolerance, and policy breach calls (independent of Quality Score)",
                           rows=rows, q=q, dept=dept, start=start, end=end)

@app.route("/score-reports")
@login_required
def score_reports():
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "All")
    score_band = request.args.get("score_band", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    
    where = ["1=1"]
    params = []
    curr_user = getattr(request, "current_user", None)
    if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
        where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
    if dept in ("Inbound", "Outbound"):
        where.append("department=?")
        params.append(dept)
    if score_band == "Low":
        where.append("quality_score < 80")
    elif score_band == "Mid":
        where.append("quality_score >= 80 AND quality_score <= 90")
    elif score_band == "Good":
        where.append("quality_score > 90")
    if q:
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR call_id LIKE ?)")
        params += [f"%{q}%"] * 3
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)

    sql = "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY quality_score ASC LIMIT 500"
    with db() as con:
        rows = con.execute(sql, params).fetchall()

    return render_template("audits_view.html",
                           page="score_reports",
                           title="Quality Score Reports",
                           subtitle="Detailed score category breakdown (Low <80%, Average 80-90%, Good >90%)",
                           show_score_filter=True,
                           score_band=score_band,
                           rows=rows, q=q, dept=dept, start=start, end=end)

@app.route("/auditors")
@roles("admin", "quality", "manager")
def auditors_view():
    with db() as con:
        rows = con.execute("""
            SELECT auditor_name, COUNT(*) as total_audits,
                   ROUND(AVG(quality_score), 2) as average_score,
                   SUM(is_fatal) as fatal_calls,
                   SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_scores,
                   SUM(CASE WHEN quality_score > 90 THEN 1 ELSE 0 END) as good_scores
            FROM audits WHERE auditor_name IS NOT NULL AND auditor_name != ''
            GROUP BY auditor_name ORDER BY total_audits DESC
        """).fetchall()
    return render_template("auditors.html", page="auditors", rows=rows)

# --- Routes: Advisor 360 Search ---
@app.route("/advisor-search")
@roles("admin", "quality", "manager")
def advisor_search():
    emp_id = request.args.get("emp_id", "").strip()
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    fatal_filter = request.args.get("fatal_filter", "All")
    score_filter = request.args.get("score_filter", "All")

    profile = None
    if emp_id:
        where = ["(advisor_id = ? OR advisor_name LIKE ?)"]
        params = [emp_id, f"%{emp_id}%"]
        
        if dept in ("Inbound", "Outbound"):
            where.append("department=?")
            params.append(dept)
        if start:
            where.append("COALESCE(call_date, evaluation_date) >= ?")
            params.append(start)
        if end:
            where.append("COALESCE(call_date, evaluation_date) <= ?")
            params.append(end)
        if fatal_filter == "Fatal":
            where.append("is_fatal=1")
        elif fatal_filter == "Non-Fatal":
            where.append("is_fatal=0")
        if score_filter == "Low":
            where.append("quality_score < 80")
        elif score_filter == "Mid":
            where.append("quality_score >= 80 AND quality_score <= 90")
        elif score_filter == "Good":
            where.append("quality_score > 90")

        with db() as con:
            audits_list = con.execute("SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC", params).fetchall()
            warnings_list = con.execute("SELECT * FROM warnings WHERE (advisor_id=? OR advisor_name LIKE ?) ORDER BY issue_date DESC", (emp_id, f"%{emp_id}%")).fetchall()

        if audits_list or warnings_list:
            total_calls = len(audits_list)
            fatal_calls = sum(a["is_fatal"] for a in audits_list)
            low_score_calls = sum(a["quality_score"] < 80 for a in audits_list if a["quality_score"] is not None)
            mid_score_calls = sum(80 <= a["quality_score"] <= 90 for a in audits_list if a["quality_score"] is not None)
            good_score_calls = sum(a["quality_score"] > 90 for a in audits_list if a["quality_score"] is not None)
            avg_score = round(sum(a["quality_score"] for a in audits_list if a["quality_score"] is not None) / max(1, total_calls), 2)
            fatal_rate = (fatal_calls / max(1, total_calls)) * 100

            primary_name = audits_list[0]["advisor_name"] if audits_list else (warnings_list[0]["advisor_name"] if warnings_list else emp_id)
            primary_id = audits_list[0]["advisor_id"] if audits_list and audits_list[0]["advisor_id"] else emp_id

            profile = {
                "advisor_name": primary_name,
                "advisor_id": primary_id,
                "departments": list(set(a["department"] for a in audits_list if a["department"])),
                "team_leaders": list(set(a["team_leader"] for a in audits_list if a["team_leader"])),
                "locations": list(set(a["location"] for a in audits_list if a["location"])),
                "total_calls": total_calls,
                "avg_score": avg_score,
                "fatal_calls": fatal_calls,
                "fatal_rate": fatal_rate,
                "low_score_calls": low_score_calls,
                "mid_score_calls": mid_score_calls,
                "good_score_calls": good_score_calls,
                "audits": audits_list,
                "warnings": warnings_list
            }

    return render_template("advisor_search.html",
                           page="advisor_search",
                           query_id=emp_id,
                           profile=profile,
                           dept=dept, start=start, end=end,
                           fatal_filter=fatal_filter, score_filter=score_filter)

# --- Routes: Export Center ---
@app.route("/export-center")
@login_required
def export_center():
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    auditor = request.args.get("auditor", "")
    
    # Non-admin / non-manager users can only export their own records if advisor_id assigned
    curr_user = request.current_user
    restrict_advisor = curr_user["advisor_id"] if (curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]) else None

    with db() as con:
        datasets = export_service.build_export_datasets(con, start_date=start, end_date=end, department=dept, auditor=auditor, current_advisor_id=restrict_advisor)
        counts = {k: len(v) for k, v in datasets.items()}
        auditors_list = [r[0] for r in con.execute("SELECT DISTINCT auditor_name FROM audits WHERE auditor_name IS NOT NULL AND auditor_name != '' ORDER BY auditor_name").fetchall()]

    return render_template("export_center.html",
                           page="exports",
                           counts=counts,
                           auditors_list=auditors_list,
                           selected_auditor=auditor,
                           dept=dept, start=start, end=end)

@app.route("/export/single/<key>")
@login_required
def export_single(key):
    fmt = request.args.get("format", "xlsx")
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    auditor = request.args.get("auditor", "")

    curr_user = request.current_user
    restrict_advisor = curr_user["advisor_id"] if (curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]) else None

    with db() as con:
        datasets = export_service.build_export_datasets(con, start_date=start, end_date=end, department=dept, auditor=auditor, current_advisor_id=restrict_advisor)
        if key not in datasets:
            flash(f"Requested dataset '{key}' does not exist.", "error")
            return redirect(url_for("export_center"))
        df = datasets[key]

    clean_name = key.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("<", "lt").replace(">", "gt").replace("-", "_").replace("%", "pct")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    
    log_audit("EXPORT_REPORT", key, f"Format: {fmt}, Auditor: {auditor or 'All'}, Rows: {len(df)}")
    
    if fmt == "csv":
        output = io.BytesIO(df.to_csv(index=False).encode("utf-8"))
        return send_file(output, mimetype="text/csv", as_attachment=True, download_name=f"{clean_name}_{timestamp}.csv")
    else:
        output = export_service.dataframe_to_styled_excel(df, sheet_name=key)
        return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                         as_attachment=True, download_name=f"{clean_name}_{timestamp}.xlsx")

@app.route("/export/combined")
@login_required
def export_combined():
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    auditor = request.args.get("auditor", "")

    curr_user = request.current_user
    restrict_advisor = curr_user["advisor_id"] if (curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]) else None

    with db() as con:
        datasets = export_service.build_export_datasets(con, start_date=start, end_date=end, department=dept, auditor=auditor, current_advisor_id=restrict_advisor)
        output = export_service.generate_combined_excel({
            "Inbound Audits": datasets["Inbound Audits"],
            "Outbound Audits": datasets["Outbound Audits"],
            "Fatal Calls": datasets["Fatal Calls"],
            "Low Score Calls": datasets["Low Score (<80%)"],
            "Good Calls": datasets["Good Calls (>90%)"],
            "Auditor Summary": datasets["Auditor Summary"],
            "Advisor Summary": datasets["Advisor Summary"],
            "Warning and PIP History": datasets["Warning & PIP History"],
            "Email History": datasets["Email History"]
        })

    log_audit("EXPORT_COMBINED_WORKBOOK", "Master", f"Auditor: {auditor or 'All'}, Multi-sheet workbook exported")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"MSEDCL_Master_Audit_Report_{timestamp}.xlsx")

@app.route("/export/zip")
@login_required
def export_zip():
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    auditor = request.args.get("auditor", "")

    curr_user = request.current_user
    restrict_advisor = curr_user["advisor_id"] if (curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]) else None

    with db() as con:
        datasets = export_service.build_export_datasets(con, start_date=start, end_date=end, department=dept, auditor=auditor, current_advisor_id=restrict_advisor)
        zip_buffer = export_service.create_all_reports_zip(datasets)

    log_audit("EXPORT_ZIP_ARCHIVE", "All Reports", f"Auditor: {auditor or 'All'}, Packaged complete zip archive")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    return send_file(zip_buffer, mimetype="application/zip", as_attachment=True, download_name=f"MSEDCL_Quality_Audit_All_Reports_{timestamp}.zip")

@app.route("/export/advisor/<emp_id>")
@login_required
def export_advisor_report(emp_id):
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")

    with db() as con:
        where = ["(advisor_id = ? OR advisor_name LIKE ?)"]
        params = [emp_id, f"%{emp_id}%"]
        if dept in ("Inbound", "Outbound"):
            where.append("department=?")
            params.append(dept)
        if start:
            where.append("COALESCE(call_date, evaluation_date) >= ?")
            params.append(start)
        if end:
            where.append("COALESCE(call_date, evaluation_date) <= ?")
            params.append(end)

        audits_df = pd.read_sql_query("SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC", con, params=params)
        warn_df = pd.read_sql_query("SELECT * FROM warnings WHERE (advisor_id=? OR advisor_name LIKE ?) ORDER BY issue_date DESC", con, params=(emp_id, f"%{emp_id}%"))

    output = export_service.generate_combined_excel({
        "Audited Calls": audits_df,
        "Warnings and PIPs": warn_df
    })
    log_audit("EXPORT_ADVISOR_PROFILE", emp_id, f"Exported individual advisor report ({len(audits_df)} calls)")
    return send_file(output, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=f"Advisor_Report_{emp_id}_{datetime.now().strftime('%Y%m%d')}.xlsx")

# --- Routes: Warnings & PIP Module ---
@app.route("/warnings", methods=["GET", "POST"])
@roles("admin", "quality", "manager", "viewer", "advisor")
def warnings():
    curr_user = request.current_user
    if request.method == "POST":
        if curr_user["role"] in ("viewer", "advisor"):
            abort(403)
        advisor_name = request.form.get("advisor_name", "").strip()
        advisor_id = request.form.get("advisor_id", "").strip() or None
        department = request.form.get("department", "Inbound")
        warning_type = request.form.get("warning_type", "").strip()
        issue_date = request.form.get("issue_date") or date.today().isoformat()
        review_date = request.form.get("review_date") or None
        recipient = request.form.get("recipient_email", "").strip()
        subject = request.form.get("subject", "").strip()
        body_text = request.form.get("body", "").strip()
        remarks = request.form.get("remarks", "").strip()
        status = request.form.get("status", "Open")

        if not advisor_name or not recipient or not subject or not body_text:
            flash("Advisor name, recipient email, subject, and body content are required.", "error")
        else:
            with db() as con:
                cur = con.execute("""
                    INSERT INTO warnings(advisor_id, advisor_name, department, warning_type, issue_date,
                    review_date, recipient_email, subject, body, email_status, status, created_by, remarks, created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (advisor_id, advisor_name, department, warning_type, issue_date, review_date,
                      recipient, subject, body_text, "Draft", status, request.current_user["username"], remarks))
                wid = cur.lastrowid
            log_audit("CREATE_WARNING_DRAFT", f"#{wid}", f"Advisor: {advisor_name}, Type: {warning_type}")
            flash(f"Warning draft #{wid} successfully created and registered.", "success")
        return redirect(url_for("warnings"))

    with db() as con:
        if curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
            rows = con.execute("""
                SELECT * FROM warnings 
                WHERE (advisor_id=? OR advisor_name LIKE ?) 
                ORDER BY id DESC
            """, (curr_user["advisor_id"], f"%{curr_user['advisor_id']}%")).fetchall()
        else:
            rows = con.execute("SELECT * FROM warnings ORDER BY id DESC").fetchall()

    return render_template("warnings.html", page="warnings", rows=rows, today=date.today().isoformat())

@app.route("/warnings/<int:warning_id>/send", methods=["POST"])
@roles("admin", "quality", "manager")
def send_warning(warning_id):
    with db() as con:
        w = con.execute("SELECT * FROM warnings WHERE id=?", (warning_id,)).fetchone()
    if not w:
        abort(404)
        
    host = get_setting("smtp_host") or os.environ.get("SMTP_HOST")
    port = int(get_setting("smtp_port") or os.environ.get("SMTP_PORT", "587"))
    username = get_setting("smtp_username") or os.environ.get("SMTP_USERNAME")
    password = get_setting("smtp_password") or os.environ.get("SMTP_PASSWORD")
    sender = get_setting("smtp_from") or os.environ.get("SMTP_FROM", username or "quality@msedcl.in")

    if not all([host, username, password, sender, w["recipient_email"]]):
        flash("Email dispatch failed: Incomplete email configuration. Please check Admin Settings.", "error")
        with db() as con:
            con.execute("UPDATE warnings SET email_status='Failed', remarks=COALESCE(remarks,'') || ' [Incomplete SMTP Config]' WHERE id=?", (warning_id,))
            con.execute("""
                INSERT INTO email_history (warning_id, created_at, employee_id, advisor_name, department,
                recipient_email, email_type, subject, body, created_by, sent_status, error_details)
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, 'Failed', 'SMTP credentials not configured')
            """, (warning_id, w["advisor_id"], w["advisor_name"], w["department"], w["recipient_email"],
                  w["warning_type"], w["subject"], w["body"], request.current_user["username"]))
        return redirect(url_for("warnings"))

    try:
        msg = EmailMessage()
        msg["Subject"] = w["subject"]
        msg["From"] = sender
        msg["To"] = w["recipient_email"]
        msg.set_content(w["body"])

        with smtplib.SMTP(host, port, timeout=20) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(username, password)
            server.send_message(msg)

        sent_time = datetime.now().isoformat(timespec="seconds")
        with db() as con:
            con.execute("UPDATE warnings SET email_status='Sent', sent_at=? WHERE id=?", (sent_time, warning_id))
            con.execute("""
                INSERT INTO email_history (warning_id, created_at, employee_id, advisor_name, department,
                recipient_email, email_type, subject, body, created_by, sent_status, sent_at, delivery_status)
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, 'Sent', ?, 'Accepted by SMTP Server')
            """, (warning_id, w["advisor_id"], w["advisor_name"], w["department"], w["recipient_email"],
                  w["warning_type"], w["subject"], w["body"], request.current_user["username"], sent_time))

        log_audit("EMAIL_SENT", f"Warning #{warning_id}", f"Recipient: {w['recipient_email']}")
        flash(f"Official warning notification #{warning_id} sent to {w['recipient_email']}.", "success")
    except Exception as exc:
        err = f"Send error: {type(exc).__name__} - {str(exc)}"
        with db() as con:
            con.execute("UPDATE warnings SET email_status='Failed' WHERE id=?", (warning_id,))
            con.execute("""
                INSERT INTO email_history (warning_id, created_at, employee_id, advisor_name, department,
                recipient_email, email_type, subject, body, created_by, sent_status, error_details)
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, 'Failed', ?)
            """, (warning_id, w["advisor_id"], w["advisor_name"], w["department"], w["recipient_email"],
                  w["warning_type"], w["subject"], w["body"], request.current_user["username"], err))
        log_audit("EMAIL_FAILED", f"Warning #{warning_id}", err)
        flash(f"Email delivery failed ({type(exc).__name__}). Check settings or server logs.", "error")

    return redirect(url_for("warnings"))

@app.route("/warnings/<int:warning_id>/close", methods=["POST"])
@roles("admin", "quality", "manager")
def close_warning(warning_id):
    remarks = request.form.get("closure_remarks", "").strip()
    with db() as con:
        con.execute("""
            UPDATE warnings SET status='Closed', closure_date=datetime('now'), closure_remarks=?
            WHERE id=?
        """, (remarks, warning_id))
    log_audit("CLOSE_WARNING", f"#{warning_id}", f"Remarks: {remarks}")
    flash(f"Warning / PIP #{warning_id} marked as Closed and resolved.", "success")
    return redirect(url_for("warnings"))

@app.route("/warnings/<int:warning_id>/feedback", methods=["POST"])
@login_required
def warning_advisor_feedback(warning_id):
    feedback_text = request.form.get("feedback", "").strip()
    if not feedback_text:
        flash("Feedback text cannot be blank.", "error")
        return redirect(url_for("warnings"))

    curr_user = request.current_user
    with db() as con:
        w = con.execute("SELECT * FROM warnings WHERE id=?", (warning_id,)).fetchone()
        if not w:
            flash("Warning record not found.", "error")
            return redirect(url_for("warnings"))

        # Verify advisor only submits feedback for their own warning if role is viewer/advisor
        if curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
            if (w["advisor_id"] or "").strip() != curr_user["advisor_id"].strip():
                abort(403)

        con.execute("""
            UPDATE warnings SET advisor_feedback=?, advisor_feedback_date=datetime('now')
            WHERE id=?
        """, (feedback_text, warning_id))

    log_audit("WARNING_FEEDBACK_SUBMITTED", f"#{warning_id}", f"Advisor: {w['advisor_name']}")
    flash("Your feedback and action response has been successfully recorded.", "success")
    return redirect(url_for("warnings"))

# --- Routes: Email History ---
@app.route("/email-history")
@roles("admin", "quality", "manager")
def email_history():
    q = request.args.get("q", "").strip()
    email_type = request.args.get("email_type", "All")
    status = request.args.get("status", "All")

    where = ["1=1"]
    params = []
    if q:
        where.append("(advisor_name LIKE ? OR recipient_email LIKE ? OR subject LIKE ? OR employee_id LIKE ?)")
        params += [f"%{q}%"] * 4
    if email_type != "All":
        where.append("email_type LIKE ?")
        params.append(f"%{email_type}%")
    if status != "All":
        where.append("sent_status = ?")
        params.append(status)

    sql = "SELECT * FROM email_history WHERE " + " AND ".join(where) + " ORDER BY id DESC"
    with db() as con:
        rows = con.execute(sql, params).fetchall()

    return render_template("email_history.html", page="email_history", rows=rows, q=q, email_type=email_type, status=status)

# --- Routes: Import Files ---
@app.route("/upload", methods=["GET", "POST"])
@roles("admin", "quality")
def upload():
    import_enabled = get_setting("enable_excel_import", "true") == "true"
    result = None

    if request.method == "POST":
        if not import_enabled and request.current_user["role"] != "admin":
            flash("Workbook import has been disabled by System Administration.", "error")
            return redirect(url_for("upload"))
            
        file = request.files.get("file")
        department = request.form.get("department")
        if not file or not file.filename:
            flash("Please choose an Excel (.xlsx, .xlsm) or CSV file.", "error")
        elif department not in ("Inbound", "Outbound"):
            flash("Please select either Inbound or Outbound department.", "error")
        else:
            try:
                filename, df = read_upload(file)
                result = import_dataframe(df, department, filename, request.current_user["username"])
                flash(f"Workbook ingested: {result['inserted']} inserted, {result['duplicates']} duplicates skipped, {result['rejected']} rejected.", "success")
            except Exception as exc:
                flash(f"Ingestion failed: {str(exc)}", "error")

    with db() as con:
        import_batches = con.execute("SELECT * FROM import_batches ORDER BY uploaded_at DESC LIMIT 50").fetchall()

    return render_template("upload.html",
                           page="upload",
                           result=result,
                           import_batches=import_batches,
                           import_enabled=import_enabled,
                           max_mb=MAX_UPLOAD_MB)

@app.route("/upload/batch/<batch_id>/delete", methods=["POST"])
@roles("admin", "quality")
def delete_import_batch(batch_id):
    with db() as con:
        batch = con.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            flash("Import batch not found.", "error")
            return redirect(url_for("upload"))
        # Delete associated audits and batch entry
        deleted_audits = con.execute("DELETE FROM audits WHERE import_batch=?", (batch_id,)).rowcount
        con.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))
    
    log_audit("DELETE_IMPORT_BATCH", batch_id, f"File: {batch['source_file']}, Deleted {deleted_audits} audits")
    flash(f"Import batch '{batch['source_file']}' removed along with {deleted_audits} associated audit records.", "success")
    return redirect(url_for("upload"))

@app.route("/api/advisor/lookup")
@login_required
def advisor_lookup():
    q = request.args.get("query", "").strip()
    if not q or len(q) < 2:
        return {"found": False}
    with db() as con:
        r = con.execute("""
            SELECT advisor_id, advisor_name, department, team_leader,
                   COUNT(*) as total_calls,
                   SUM(is_fatal) as fatals,
                   SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_calls,
                   ROUND(AVG(quality_score), 2) as avg_score,
                   (SELECT call_id || ' - ' || COALESCE(fatal_category, error_categories, 'Observation: ' || SUBSTR(observation, 1, 40))
                    FROM audits WHERE (advisor_id = ? OR advisor_name LIKE ?) AND (is_fatal=1 OR quality_score < 80)
                    ORDER BY id DESC LIMIT 1) as recent_incident
            FROM audits
            WHERE advisor_id = ? OR advisor_name LIKE ?
            GROUP BY COALESCE(advisor_id, advisor_name)
            ORDER BY total_calls DESC LIMIT 1
        """, (q, f"%{q}%", q, f"%{q}%")).fetchone()

    if r:
        email_prefix = (r["advisor_id"] or r["advisor_name"].split()[0]).lower().replace(" ", ".")
        suggested_email = f"{email_prefix}@msedcl.in"
        inc_text = r["recent_incident"] or "No recent fatal incident on record."
        
        # Build intelligent pre-filled notice body
        suggested_body = (
            f"Dear {r['advisor_name']},\n\n"
            f"This communication serves as formal quality feedback regarding your performance on recent customer interactions in the {r['department']} department.\n\n"
            f"• Performance Summary:\n"
            f"  - Total Evaluated Calls: {r['total_calls']}\n"
            f"  - Average Quality Score: {r['avg_score']}%\n"
            f"  - Fatal Infractions Recorded: {r['fatals'] or 0}\n"
            f"  - Low Score Interactions (<80%): {r['low_calls'] or 0}\n"
            f"  - Key Incident Reference: {inc_text}\n\n"
            f"Please review the recorded observations with your Team Leader ({r['team_leader'] or 'Supervisory Team'}) and initiate corrective action within 7 operational days.\n\n"
            f"Regards,\n"
            f"MSEDCL Quality Assurance Management"
        )
        
        return {
            "found": True,
            "advisor_name": r["advisor_name"],
            "advisor_id": r["advisor_id"] or "",
            "department": r["department"] or "Inbound",
            "suggested_email": suggested_email,
            "suggested_subject": f"MSEDCL Quality Assurance Notice - {r['advisor_name']} ({r['advisor_id'] or 'Staff'})",
            "suggested_body": suggested_body,
            "remarks": f"Total Calls: {r['total_calls']}, Avg: {r['avg_score']}%, Fatals: {r['fatals'] or 0}, Low Scores: {r['low_calls'] or 0}"
        }
    return {"found": False}

# --- Routes: Admin User Management ---
@app.route("/admin/users")
@roles("admin", "manager")
def user_management():
    with db() as con:
        users = con.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
    return render_template("user_management.html", page="users", users=users)

@app.route("/admin/users/create", methods=["POST"])
@roles("admin", "manager")
def user_create():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "quality")
    advisor_id = request.form.get("advisor_id", "").strip() or None
    password = request.form.get("password", "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("user_management"))

    if role not in ("admin", "quality", "manager", "viewer"):
        flash("Invalid role selected.", "error")
        return redirect(url_for("user_management"))

    pwd_hash = generate_password_hash(password)
    try:
        with db() as con:
            existing = con.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
            if existing:
                flash(f"Username '{username}' is already registered.", "error")
                return redirect(url_for("user_management"))

            con.execute("""
                INSERT INTO users (username, password_hash, role, advisor_id, active, full_name, email, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, datetime('now'))
            """, (username, pwd_hash, role, advisor_id, full_name, email))
        log_audit("CREATE_USER", username, f"Role: {role}, AdvisorID: {advisor_id}, Email: {email}")
        flash(f"User '{username}' created successfully.", "success")
    except sqlite3.IntegrityError as e:
        flash(f"Could not create user: {e}", "error")

    return redirect(url_for("user_management"))

@app.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@roles("admin", "manager")
def user_toggle_status(user_id):
    if user_id == request.current_user["id"]:
        flash("You cannot deactivate your own account.", "error")
        return redirect(url_for("user_management"))
    with db() as con:
        u = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if u:
            new_status = 0 if u["active"] else 1
            con.execute("UPDATE users SET active=? WHERE id=?", (new_status, user_id))
            log_audit("TOGGLE_USER_STATUS", u["username"], f"New active status: {new_status}")
            flash(f"User '{u['username']}' status updated to {'Active' if new_status else 'Inactive'}.", "success")
    return redirect(url_for("user_management"))

@app.route("/admin/users/reset-password", methods=["POST"])
@roles("admin")
def user_reset_password():
    user_id = request.form.get("user_id")
    new_password = request.form.get("new_password")
    if not user_id or not new_password:
        flash("Missing user ID or password.", "error")
        return redirect(url_for("user_management"))
    pwd_hash = generate_password_hash(new_password)
    with db() as con:
        con.execute("UPDATE users SET password_hash=? WHERE id=?", (pwd_hash, user_id))
        u = con.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
    log_audit("RESET_PASSWORD", u["username"] if u else user_id, "Administrator reset password")
    flash(f"Password reset successfully for user.", "success")
    return redirect(url_for("user_management"))

@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@roles("admin")
def user_delete(user_id):
    if user_id == request.current_user["id"]:
        flash("You cannot delete your own account.", "error")
        return redirect(url_for("user_management"))
    with db() as con:
        u = con.execute("SELECT username FROM users WHERE id=?", (user_id,)).fetchone()
        if u:
            con.execute("DELETE FROM users WHERE id=?", (user_id,))
            log_audit("DELETE_USER", u["username"], "User account deleted")
            flash(f"User '{u['username']}' deleted.", "success")
    return redirect(url_for("user_management"))

# --- Routes: Admin Settings & Auditing ---
@app.route("/admin/settings", methods=["GET", "POST"])
@roles("admin")
def settings_page():
    if request.method == "POST":
        updates = [
            ("company_name", request.form.get("company_name", "").strip()),
            ("admin_email", request.form.get("admin_email", "").strip()),
            ("default_date_range", request.form.get("default_date_range", "30").strip()),
            ("enable_excel_import", request.form.get("enable_excel_import", "true")),
            ("email_provider", request.form.get("email_provider", "smtp")),
            ("smtp_host", request.form.get("smtp_host", "").strip()),
            ("smtp_port", request.form.get("smtp_port", "587").strip()),
            ("smtp_username", request.form.get("smtp_username", "").strip()),
            ("smtp_password", request.form.get("smtp_password", "").strip()),
            ("smtp_from", request.form.get("smtp_from", "").strip()),
            ("ms_client_id", request.form.get("ms_client_id", "").strip()),
            ("ms_tenant_id", request.form.get("ms_tenant_id", "").strip()),
            ("ms_client_secret", request.form.get("ms_client_secret", "").strip())
        ]
        with db() as con:
            for k, v in updates:
                con.execute("""
                    INSERT INTO system_settings (setting_key, setting_value, updated_at, updated_by)
                    VALUES (?, ?, datetime('now'), ?)
                    ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value=excluded.setting_value, updated_at=datetime('now'), updated_by=excluded.updated_by
                """, (k, v, request.current_user["username"]))
        log_audit("UPDATE_SETTINGS", "System", "Settings updated by administrator")
        flash("System settings successfully saved.", "success")
        return redirect(url_for("settings_page"))

    with db() as con:
        rows = con.execute("SELECT setting_key, setting_value FROM system_settings").fetchall()
        settings_dict = {r["setting_key"]: r["setting_value"] for r in rows}

    return render_template("settings.html", page="settings", settings=settings_dict)

@app.route("/admin/settings/test-email", methods=["POST"])
@roles("admin")
def test_email_connection():
    recipient = request.form.get("test_recipient", "").strip()
    if not recipient:
        flash("Please enter a test recipient email.", "error")
        return redirect(url_for("settings_page"))
        
    host = get_setting("smtp_host") or os.environ.get("SMTP_HOST")
    port = int(get_setting("smtp_port") or os.environ.get("SMTP_PORT", "587"))
    username = get_setting("smtp_username") or os.environ.get("SMTP_USERNAME")
    password = get_setting("smtp_password") or os.environ.get("SMTP_PASSWORD")
    sender = get_setting("smtp_from") or os.environ.get("SMTP_FROM", username or "quality@msedcl.in")

    if not all([host, username, password]):
        flash("Test failed: SMTP Host, Username, or Password missing in configuration.", "error")
        return redirect(url_for("settings_page"))

    try:
        msg = EmailMessage()
        msg["Subject"] = "MSEDCL Quality Audit System - Diagnostic Test"
        msg["From"] = sender
        msg["To"] = recipient
        msg.set_content("This is an automated diagnostic test message confirming SMTP connectivity from the MSEDCL Quality Audit Portal.")

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(username, password)
            server.send_message(msg)

        log_audit("EMAIL_TEST_SUCCESS", recipient, "SMTP diagnostic ping succeeded")
        flash(f"Test email successfully accepted by {host} for {recipient}!", "success")
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)}"
        log_audit("EMAIL_TEST_FAILED", recipient, err)
        flash(f"Email connection test failed ({err}).", "error")

    return redirect(url_for("settings_page"))

@app.route("/admin/backup/download")
@roles("admin")
def db_backup_download():
    log_audit("DOWNLOAD_DB_BACKUP", "quality_audit.db", "Database backup downloaded")
    return send_file(DB_PATH, as_attachment=True, download_name=f"quality_audit_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")

@app.route("/admin/audit-logs")
@roles("admin")
def audit_logs():
    with db() as con:
        logs = con.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("audit_logs.html", page="audit_logs", logs=logs)

@app.route("/admin/audits/purge", methods=["POST"])
@roles("admin")
def purge_uploaded_audits():
    with db() as con:
        deleted_audits = con.execute("DELETE FROM audits").rowcount
        deleted_batches = con.execute("DELETE FROM import_batches").rowcount
    log_audit("PURGE_AUDITS", "audits", f"Purged {deleted_audits} audits and {deleted_batches} batch records. Users preserved.")
    flash(f"Successfully purged {deleted_audits} historical audit records and {deleted_batches} batch logs. All user accounts and system configuration remain intact.", "success")
    return redirect(url_for("settings_page"))

@app.route("/admin/restart", methods=["POST"])
@roles("admin")
def admin_restart_system():
    admin_password = request.form.get("admin_password", "")
    current_admin = request.current_user

    # Strict password check for authorizing system restart
    with db() as con:
        u = con.execute("SELECT password_hash FROM users WHERE id=?", (current_admin["id"],)).fetchone()

    if not u or not check_password_hash(u["password_hash"], admin_password):
        log_audit("RESTART_FAILED", "System", "Failed restart attempt: Invalid administrator password")
        flash("Authorization failed: Incorrect administrator password. System restart aborted.", "error")
        return redirect(request.referrer or url_for("settings_page"))

    # Log successful authorization
    log_audit("RESTART_AUTHORIZED", "System", f"Full system restart triggered by administrator '{current_admin['username']}'")
    flash("Administrator password verified. System is restarting now...", "success")

    def delayed_restart():
        time.sleep(1.2)
        try:
            # Spawn fresh python process running app.py
            subprocess.Popen([sys.executable, "app.py"], cwd=os.path.dirname(os.path.abspath(__file__)))
        except Exception as e:
            print(f"Failed to spawn new process: {e}")
        finally:
            os._exit(0)

    threading.Thread(target=delayed_restart, daemon=True).start()
    return render_template("restarting.html")

# --- Error Handlers ---
@app.errorhandler(403)
def forbidden(e):
    return "<h2 style='font-family:sans-serif;'>403 Forbidden: You do not possess authorized permissions to access this module.</h2>", 403

@app.errorhandler(404)
def not_found(e):
    return "<h2 style='font-family:sans-serif;'>404 Not Found: The requested resource does not exist.</h2>", 404

@app.errorhandler(413)
def too_large(e):
    return f"<h2 style='font-family:sans-serif;'>413 Request Entity Too Large: File upload exceeds maximum limit ({MAX_UPLOAD_MB} MB).</h2>", 413

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
