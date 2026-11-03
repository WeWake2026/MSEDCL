import os, io, sqlite3, hashlib, secrets, smtplib, ssl, json, sys, time, threading, subprocess, csv
from email.message import EmailMessage
from datetime import datetime, date, timedelta
from functools import wraps
import dateutil.parser
import openpyxl

from flask import Flask, request, redirect, url_for, render_template, session, flash, send_file, abort, Response, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import export_service
import db_explorer_service

# Load environment configuration
env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_file):
    with open(env_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

APP_SECRET = os.environ.get("APP_SECRET_KEY")
if not APP_SECRET:
    # Use consistent static secret fallback so multiple gunicorn workers can decode each other's session cookies
    APP_SECRET = "msedcl-qa-audit-secret-key-2026-production-render"
    os.environ["APP_SECRET_KEY"] = APP_SECRET

DB_PATH = os.environ.get("DATABASE_PATH", "quality_audit.db")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))

# Auto-initialize database schema if database or tables don't exist yet
try:
    from init_db import init_db
    init_db(DB_PATH)
except Exception as _e:
    print(f"Warning initializing database: {_e}")

from database import get_db, is_postgres, check_db_health, DbIntegrityError

def get_max_upload_mb():
    try:
        val = get_setting("max_upload_size_mb")
        if val and str(val).strip():
            return int(str(val).strip())
    except Exception:
        pass
    return MAX_UPLOAD_MB

app = Flask(__name__)
app.secret_key = APP_SECRET
app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
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

@app.route("/healthz")
@app.route("/api/health")
def healthz():
    status = check_db_health()
    http_code = 200 if status["healthy"] else 503
    return status, http_code

# --- Database helpers ---
def db():
    return get_db(DB_PATH)

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
    curr_user = getattr(request, "current_user", None)
    unread_notifs = 0
    topbar_notifs = []
    
    if curr_user:
        try:
            with db() as con:
                # Count unread notifications targeted directly to user, their role, or broadcast to all
                unread_res = con.execute("""
                    SELECT COUNT(*) as cnt FROM notifications 
                    WHERE is_read = 0 AND (user_id = ? OR target_role = ? OR target_role = 'all' OR (target_role IS NULL AND user_id IS NULL))
                """, (curr_user["id"], curr_user["role"])).fetchone()
                unread_notifs = unread_res["cnt"] if unread_res else 0

                topbar_notifs = con.execute("""
                    SELECT id, title, message, notif_type, link_url, created_at, is_read 
                    FROM notifications 
                    WHERE (user_id = ? OR target_role = ? OR target_role = 'all' OR (target_role IS NULL AND user_id IS NULL))
                    ORDER BY id DESC LIMIT 5
                """, (curr_user["id"], curr_user["role"])).fetchall()
        except Exception:
            unread_notifs = 0
            topbar_notifs = []

    return {
        "user": curr_user,
        "app_name": get_setting("company_name", "MSEDCL Quality Audit"),
        "import_enabled": get_setting("enable_excel_import", "true") == "true",
        "unread_notif_count": unread_notifs,
        "topbar_notifications": topbar_notifs,
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
    "error_categories": ["error categories", "error category", "error type", "major error", "error", "area of improvement", "areas of improvement", "improvement areas", "improvement area"],
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
    if value is None or str(value).strip() == "" or str(value).strip().lower() == "nan":
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
    if value is None or str(value).strip() == "" or str(value).strip().lower() == "nan":
        return None
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    try:
        dt = dateutil.parser.parse(str(value).strip(), dayfirst=True)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return None


LIVE_CALL_ALIASES = {
    "call_id": ["call id", "session id", "track id", "interaction id", "call_id", "sr no", "sr. no", "sr no."],
    "call_date": ["date", "call date", "date of call", "interaction date"],
    "advisor_id": ["advisor id", "employee id", "agent id", "emp id", "advisor code", "employee code"],
    "advisor_name": ["advisor name", "employee name", "agent name", "name of advisor", "employee"],
    "calling_no": ["calling no", "calling number", "contact number", "mobile number", "caller no", "phone number", "contact no"],
    "call_disposition": ["call disposition", "consumer concern", "disposition", "concern", "complaint type", "type of complaint"],
    "observation": ["call observation", "observation", "call observations", "remarks", "auditor remarks"],
    "improvement": ["area of improvement", "improvement", "areas of improvement", "improvement areas", "coaching feedback"],
    "feedback": ["feedback", "status", "action", "monitoring status"],
    "location": ["location", "site", "center", "city"],
    "auditor_name": ["auditor name", "qa name", "evaluator name", "evaluator", "quality auditor", "qa"]
}

def detect_live_call_columns(columns):
    normalized = {norm(c): c for c in columns}
    mapping = {}
    for field, aliases in LIVE_CALL_ALIASES.items():
        for alias in aliases:
            if norm(alias) in normalized:
                mapping[field] = normalized[norm(alias)]
                break
    return mapping

def is_live_call_workbook(headers, data_rows):
    live_map = detect_live_call_columns(headers)
    audit_map = detect_columns(headers)
    # If explicitly has calling_no or call_disposition or feedback AND lacks quality_score, it's live calls
    if "quality_score" not in audit_map:
        if "calling_no" in live_map or "call_disposition" in live_map or "feedback" in live_map:
            return True
    return False

def make_live_call_hash(dept, row):
    key = "|".join(str(row.get(k) or "").strip().lower() for k in
                   ["call_id", "call_date", "advisor_id", "advisor_name", "calling_no", "call_disposition", "auditor_name"])
    if not (row.get("call_id") or "").strip():
        key += "|" + str(row.get("observation") or "").strip().lower()
    return hashlib.sha256((dept.lower() + "|live|" + key).encode("utf-8")).hexdigest()

def import_live_calls(headers, data_rows, department, filename, username, duplicate_handling="skip"):
    mapping = detect_live_call_columns(headers)
    if "advisor_name" not in mapping:
        raise ValueError("Advisor Name column was not detected in live call file. Please verify headers.")
    
    batch_id = secrets.token_hex(8)
    seen, inserted, duplicates, rejected = len(data_rows), 0, 0, 0
    errors = []
    duplicate_rows = []
    rejected_rows = []
    now = datetime.now().isoformat(timespec="seconds")
    
    with db() as con:
        for ix, source_row in enumerate(data_rows):
            row_num = ix + 2
            row = {}
            for field, source_col in mapping.items():
                val = source_row.get(source_col)
                row[field] = None if val is None or str(val).strip() == "" or str(val).strip().lower() == "nan" else val
            
            raw_record = {k: (None if v is None else str(v).strip()) for k, v in row.items()}
            advisor = str(row.get("advisor_name") or "").strip()
            
            if not advisor:
                rejected += 1
                reason = "Missing advisor name"
                errors.append({"row": row_num, "reason": reason})
                rejected_rows.append({"row": row_num, "reason": reason, "data": raw_record})
                continue
            
            record = {
                "department": department,
                "call_id": str(row.get("call_id") or "").strip() or None,
                "call_date": parse_date(row.get("call_date")) or (str(row.get("call_date")).strip() if row.get("call_date") else None),
                "advisor_id": str(row.get("advisor_id") or "").strip() or None,
                "advisor_name": advisor,
                "calling_no": str(row.get("calling_no") or "").strip() or None,
                "call_disposition": str(row.get("call_disposition") or "").strip() or None,
                "observation": str(row.get("observation") or "").strip() or None,
                "improvement": str(row.get("improvement") or "").strip() or None,
                "feedback": str(row.get("feedback") or "").strip() or None,
                "location": str(row.get("location") or "").strip() or None,
                "auditor_name": str(row.get("auditor_name") or "").strip() or None,
                "source_file": filename,
                "import_batch": batch_id,
                "imported_at": now
            }
            row_hash = make_live_call_hash(department, record)
            if duplicate_handling == "force_insert":
                row_hash = row_hash[:56] + secrets.token_hex(4)
                
            is_dup = False
            sp_name = f"sp_live_{ix}"
            is_pg = getattr(con, "_is_pg", False)
            raw_conn = getattr(con, "_conn", None)
            if is_pg and raw_conn:
                try:
                    with raw_conn.cursor() as sp_cur:
                        sp_cur.execute(f"SAVEPOINT {sp_name}")
                except Exception:
                    pass

            try:
                cur = con.execute("""INSERT OR IGNORE INTO live_calls(department,call_id,call_date,advisor_id,advisor_name,
                calling_no,call_disposition,observation,improvement,feedback,location,auditor_name,
                source_file,import_batch,row_hash,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (department, record["call_id"], record["call_date"], record["advisor_id"], record["advisor_name"],
                 record["calling_no"], record["call_disposition"], record["observation"], record["improvement"],
                 record["feedback"], record["location"], record["auditor_name"], filename, batch_id, row_hash, now))
                if getattr(cur, "rowcount", 0) > 0:
                    inserted += 1
                    if is_pg and raw_conn:
                        try:
                            with raw_conn.cursor() as sp_cur:
                                sp_cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                        except Exception:
                            pass
                else:
                    is_dup = True
                    if is_pg and raw_conn:
                        try:
                            with raw_conn.cursor() as sp_cur:
                                sp_cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                        except Exception:
                            pass
            except Exception as e:
                if is_pg and raw_conn:
                    try:
                        with raw_conn.cursor() as sp_cur:
                            sp_cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    except Exception:
                        try:
                            raw_conn.rollback()
                        except Exception:
                            pass
                
                is_integrity = any(k in str(type(e).__name__).lower() for k in ["integrity", "unique", "duplicate"])
                if is_integrity:
                    is_dup = True
                else:
                    rejected += 1
                    raw_msg = str(e).strip()
                    clean_msg = raw_msg.split("\n")[0].strip()
                    if clean_msg.startswith("ERROR:"):
                        clean_msg = clean_msg[6:].strip()
                    if not clean_msg or "current transaction is aborted" in clean_msg:
                        clean_msg = "Database constraint or data format mismatch"
                    reason = f"Database error: {clean_msg[:120]}"
                    errors.append({"row": row_num, "reason": reason})
                    rejected_rows.append({"row": row_num, "reason": reason, "data": raw_record})
                    continue
                
            if is_dup:
                duplicates += 1
                duplicate_rows.append({"row": row_num, "reason": "Duplicate live call record", "data": raw_record})
                
        report = {
            "type": "live_calls",
            "errors": errors,
            "mapping": mapping,
            "duplicate_rows": duplicate_rows,
            "rejected_rows": rejected_rows
        }
        con.execute("""INSERT INTO import_batches(id,department,source_file,uploaded_by,uploaded_at,rows_seen,
        rows_inserted,duplicates,rejected,report_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (batch_id, department, filename, username, now, seen, inserted, duplicates, rejected, json.dumps(report, default=str)))
        
    log_audit("IMPORT_LIVE_CALLS", filename, f"Dept: {department}, Inserted: {inserted}, Dups: {duplicates}, Rejected: {rejected}")
    return {
        "batch_id": batch_id,
        "type": "live_calls",
        "seen": seen,
        "inserted": inserted,
        "duplicates": duplicates,
        "rejected": rejected,
        "mapping": mapping,
        "errors": errors,
        "duplicate_rows": duplicate_rows,
        "rejected_rows": rejected_rows
    }

def read_upload(file_storage):
    filename = secure_filename(file_storage.filename or "upload.xlsx")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload .xlsx, .xlsm or .csv files only.")
    raw = file_storage.read()
    
    rows = []
    if ext == ".csv":
        text = raw.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        for r in reader:
            if any(cell.strip() for cell in r):
                rows.append([c.strip() for c in r])
    else:
        wb = openpyxl.load_workbook(io.BytesIO(raw), data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            if any(cell is not None and str(cell).strip() != "" for cell in row):
                cleaned = []
                for cell in row:
                    if cell is None:
                        cleaned.append(None)
                    elif isinstance(cell, (datetime, date)):
                        cleaned.append(cell.strftime("%Y-%m-%d"))
                    else:
                        cleaned.append(str(cell).strip())
                rows.append(cleaned)
                
    if not rows or len(rows) < 2:
        raise ValueError("The uploaded file has no data rows.")
        
    headers = [str(h or "").strip() for h in rows[0]]
    data_rows = []
    for r in rows[1:]:
        row_dict = {}
        for idx, h in enumerate(headers):
            val = r[idx] if idx < len(r) else None
            row_dict[h] = val
        data_rows.append(row_dict)
        
    return filename, headers, data_rows

def make_hash(dept, row):
    key = "|".join(str(row.get(k) or "").strip().lower() for k in
                   ["call_id", "call_date", "evaluation_date", "advisor_id", "advisor_name", "quality_score", "is_fatal"])
    # If Call ID is absent, also factor in observation and auditor to prevent distinct calls with same score on same day from hashing identically
    if not (row.get("call_id") or "").strip():
        key += "|" + str(row.get("auditor_name") or "").strip().lower() + "|" + str(row.get("observation") or "").strip().lower()
    return hashlib.sha256((dept.lower() + "|" + key).encode("utf-8")).hexdigest()

def import_dataframe(headers, data_rows, department, filename, username, duplicate_handling="skip"):
    mapping = detect_columns(headers)
    if "advisor_name" not in mapping:
        raise ValueError("Advisor Name column was not detected. Please verify your column headers.")
    if "quality_score" not in mapping:
        raise ValueError("Quality Score column was not detected. Please ensure a Score header exists.")
    
    batch_id = secrets.token_hex(8)
    seen, inserted, duplicates, rejected = len(data_rows), 0, 0, 0
    errors = []
    duplicate_rows = []
    rejected_rows = []
    now = datetime.now().isoformat(timespec="seconds")
    
    with db() as con:
        for ix, source_row in enumerate(data_rows):
            row_num = ix + 2
            row = {}
            for field, source_col in mapping.items():
                val = source_row.get(source_col)
                row[field] = None if val is None or str(val).strip() == "" or str(val).strip().lower() == "nan" else val
            
            raw_record = {k: (None if v is None else str(v).strip()) for k, v in row.items()}
            advisor = str(row.get("advisor_name") or "").strip()
            score = parse_score(row.get("quality_score"))
            
            if not advisor or score is None:
                rejected += 1
                reason = "Missing advisor name" if not advisor else "Invalid quality score"
                err_entry = {"row": row_num, "reason": reason}
                errors.append(err_entry)
                rejected_rows.append({
                    "row": row_num,
                    "reason": reason,
                    "data": {
                        "call_id": raw_record.get("call_id") or "",
                        "call_date": raw_record.get("call_date") or "",
                        "evaluation_date": raw_record.get("evaluation_date") or "",
                        "advisor_id": raw_record.get("advisor_id") or "",
                        "advisor_name": advisor,
                        "team_leader": raw_record.get("team_leader") or "",
                        "auditor_name": raw_record.get("auditor_name") or "",
                        "quality_score": raw_record.get("quality_score") or "",
                        "is_fatal": raw_record.get("is_fatal") or "",
                        "fatal_category": raw_record.get("fatal_category") or "",
                        "error_categories": raw_record.get("error_categories") or "",
                        "observation": raw_record.get("observation") or "",
                        "location": raw_record.get("location") or "",
                        "audio_url": raw_record.get("audio_url") or ""
                    }
                })
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
            if duplicate_handling == "force_insert":
                row_hash = row_hash[:56] + secrets.token_hex(4)
                
            is_dup = False
            sp_name = f"sp_row_{ix}"
            is_pg = getattr(con, "_is_pg", False)
            raw_conn = getattr(con, "_conn", None)
            if is_pg and raw_conn:
                try:
                    with raw_conn.cursor() as sp_cur:
                        sp_cur.execute(f"SAVEPOINT {sp_name}")
                except Exception:
                    pass

            try:
                cur = con.execute("""INSERT OR IGNORE INTO audits(department,call_id,call_date,evaluation_date,advisor_id,advisor_name,
                team_leader,auditor_name,quality_score,is_fatal,fatal_category,error_categories,observation,location,
                audio_url,source_file,import_batch,row_hash,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (department, record["call_id"], record["call_date"], record["evaluation_date"], record["advisor_id"],
                 record["advisor_name"], record["team_leader"], record["auditor_name"], record["quality_score"],
                 record["is_fatal"], record["fatal_category"], record["error_categories"], record["observation"],
                 record["location"], record["audio_url"], filename, batch_id, row_hash, now))
                if getattr(cur, "rowcount", 0) > 0:
                    inserted += 1
                    if is_pg and raw_conn:
                        try:
                            with raw_conn.cursor() as sp_cur:
                                sp_cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                        except Exception:
                            pass
                else:
                    is_dup = True
                    if is_pg and raw_conn:
                        try:
                            with raw_conn.cursor() as sp_cur:
                                sp_cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                        except Exception:
                            pass
            except Exception as e:
                # If an error occurred on this specific row, rollback to savepoint so the transaction block remains valid
                if is_pg and raw_conn:
                    try:
                        with raw_conn.cursor() as sp_cur:
                            sp_cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                    except Exception:
                        try:
                            # If savepoint rollback failed and transaction is in error, recover transaction
                            raw_conn.rollback()
                        except Exception:
                            pass
                
                # Check if it's an integrity / duplicate error or a malformed data error
                is_integrity = any(k in str(type(e).__name__).lower() for k in ["integrity", "unique", "duplicate"])
                if is_integrity:
                    is_dup = True
                else:
                    rejected += 1
                    raw_msg = str(e).strip()
                    # Clean up PostgreSQL error message to be clear and informative
                    clean_msg = raw_msg.split("\n")[0].strip()
                    if clean_msg.startswith("ERROR:"):
                        clean_msg = clean_msg[6:].strip()
                    if not clean_msg or "current transaction is aborted" in clean_msg:
                        clean_msg = "Database constraint or data format mismatch"
                    reason = f"Database error: {clean_msg[:120]}"
                    errors.append({"row": row_num, "reason": reason})
                    rejected_rows.append({
                        "row": row_num,
                        "reason": reason,
                        "data": {
                            "call_id": record["call_id"] or "",
                            "call_date": record["call_date"] or "",
                            "evaluation_date": record["evaluation_date"] or "",
                            "advisor_id": record["advisor_id"] or "",
                            "advisor_name": record["advisor_name"],
                            "team_leader": record["team_leader"] or "",
                            "auditor_name": record["auditor_name"] or "",
                            "quality_score": record["quality_score"],
                            "is_fatal": record["is_fatal"],
                            "fatal_category": record["fatal_category"] or "",
                            "error_categories": record["error_categories"] or "",
                            "observation": record["observation"] or "",
                            "location": record["location"] or "",
                            "audio_url": record["audio_url"] or ""
                        }
                    })
                    continue
                
            if is_dup:
                duplicates += 1
                duplicate_rows.append({
                    "row": row_num,
                    "reason": "Duplicate record (identical hash found in database)",
                    "data": {
                        "call_id": record["call_id"] or "",
                        "call_date": record["call_date"] or "",
                        "evaluation_date": record["evaluation_date"] or "",
                        "advisor_id": record["advisor_id"] or "",
                        "advisor_name": record["advisor_name"],
                        "team_leader": record["team_leader"] or "",
                        "auditor_name": record["auditor_name"] or "",
                        "quality_score": record["quality_score"],
                        "is_fatal": record["is_fatal"],
                        "fatal_category": record["fatal_category"] or "",
                        "error_categories": record["error_categories"] or "",
                        "observation": record["observation"] or "",
                        "location": record["location"] or "",
                        "audio_url": record["audio_url"] or ""
                    }
                })
                
        report = {
            "errors": errors,
            "mapping": mapping,
            "duplicate_rows": duplicate_rows,
            "rejected_rows": rejected_rows
        }
        con.execute("""INSERT INTO import_batches(id,department,source_file,uploaded_by,uploaded_at,rows_seen,
        rows_inserted,duplicates,rejected,report_json) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (batch_id, department, filename, username, now, seen, inserted, duplicates, rejected, json.dumps(report, default=str)))
        
    log_audit("IMPORT_EXCEL", filename, f"Dept: {department}, Inserted: {inserted}, Dups: {duplicates}, Rejected: {rejected}")
    return {
        "batch_id": batch_id,
        "seen": seen,
        "inserted": inserted,
        "duplicates": duplicates,
        "rejected": rejected,
        "mapping": mapping,
        "errors": errors,
        "duplicate_rows": duplicate_rows,
        "rejected_rows": rejected_rows
    }

# --- Routes: Auth ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = None
        try:
            with db() as con:
                user = con.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?) AND active = 1", (username,)).fetchone()
        except Exception as e:
            print(f"[ERROR] Database error during login query: {e}", file=sys.stderr)
            flash("Database temporarily unavailable. Please try again in a few moments.", "error")
            return render_template("login.html")

        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["role"] = user["role"]
            session["username"] = user["username"]
            try:
                with db() as con:
                    con.execute("UPDATE users SET last_login=datetime('now') WHERE id=?", (user["id"],))
            except Exception:
                pass
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
    month = request.args.get("month", "").strip() # Format: YYYY-MM
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
    if month:
        where.append("COALESCE(call_date, evaluation_date) LIKE ?")
        params.append(f"{month}%")
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
        # Fetch distinct months available in audits
        available_months_raw = con.execute("""
            SELECT DISTINCT SUBSTR(COALESCE(call_date, evaluation_date), 1, 7) as m
            FROM audits
            WHERE COALESCE(call_date, evaluation_date) IS NOT NULL AND LENGTH(COALESCE(call_date, evaluation_date)) >= 7
            ORDER BY m DESC
        """).fetchall()
        available_months = [r[0] for r in available_months_raw if r[0] and len(r[0]) == 7]

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

        # Auditor activity (Top 15 with average score and fatals for deep evaluation)
        auditor_stats = con.execute("""
            SELECT auditor_name, MIN(department) as dept, COUNT(*) as cnt,
                   ROUND(AVG(quality_score), 2) as avg_score,
                   SUM(is_fatal) as fatals
            """ + sql_base + """ AND auditor_name IS NOT NULL AND auditor_name != ''
            GROUP BY auditor_name ORDER BY cnt DESC LIMIT 15
        """, params).fetchall()

        # Date timeline separated by Inbound vs Outbound
        timeline_rows = con.execute("""
            SELECT COALESCE(call_date, evaluation_date) as dt,
                   SUM(CASE WHEN department='Inbound' THEN 1 ELSE 0 END) as in_cnt,
                   SUM(CASE WHEN department='Outbound' THEN 1 ELSE 0 END) as out_cnt,
                   COUNT(*) as total_cnt,
                   ROUND(AVG(quality_score), 1) as avg_score
            """ + sql_base + """
            AND COALESCE(call_date, evaluation_date) IS NOT NULL
            GROUP BY COALESCE(call_date, evaluation_date)
            ORDER BY dt DESC LIMIT 31
        """, params).fetchall()
        timeline_rows = list(reversed(timeline_rows))

        # Advisor Summary Table
        advisor_summary = con.execute("""
            SELECT advisor_id, advisor_name, department, COUNT(*) as calls,
                   ROUND(AVG(quality_score), 2) as avg_score,
                   SUM(is_fatal) as fatals,
                   SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_calls,
                   SUM(CASE WHEN quality_score >= 80 AND quality_score <= 90 THEN 1 ELSE 0 END) as mid_calls,
                   SUM(CASE WHEN quality_score > 90 THEN 1 ELSE 0 END) as good_calls,
                   STRING_AGG(DISTINCT auditor_name, ', ') as auditors
            """ + sql_base + """
            GROUP BY advisor_id, advisor_name, department
            ORDER BY fatals DESC, low_calls DESC LIMIT 20
        """, params).fetchall()

        # Top Advisors for Chart (Ranked by quality score, min 1 audit)
        top_advisors_query = con.execute("""
            SELECT advisor_name,
                   ROUND(AVG(quality_score), 2) as avg_score,
                   COUNT(*) as cnt,
                   SUM(is_fatal) as fatals,
                   MIN(department) as dept
            """ + sql_base + """
            GROUP BY advisor_name
            ORDER BY avg_score DESC, cnt DESC LIMIT 15
        """, params).fetchall()

        # Live Calls Metrics & Chart Aggregations
        live_where = ["1=1"]
        live_params = []
        if dept in ("Inbound", "Outbound"):
            live_where.append("department=?")
            live_params.append(dept)
        if month:
            live_where.append("call_date LIKE ?")
            live_params.append(f"{month}%")
        if start:
            live_where.append("call_date >= ?")
            live_params.append(start)
        if end:
            live_where.append("call_date <= ?")
            live_params.append(end)
        if selected_auditor:
            live_where.append("auditor_name=?")
            live_params.append(selected_auditor)
        if advisor_query:
            live_where.append("(advisor_id LIKE ? OR advisor_name LIKE ?)")
            live_params.extend([f"%{advisor_query}%", f"%{advisor_query}%"])
        if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
            live_where.append("(advisor_id=? OR advisor_name LIKE ?)")
            live_params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])

        live_base = " FROM live_calls WHERE " + " AND ".join(live_where)
        total_live_calls = con.execute("SELECT COUNT(*)" + live_base, live_params).fetchone()[0]
        inbound_live_calls = con.execute("SELECT COUNT(*)" + live_base + " AND department='Inbound'", live_params).fetchone()[0]
        outbound_live_calls = con.execute("SELECT COUNT(*)" + live_base + " AND department='Outbound'", live_params).fetchone()[0]
        done_live_feedback = con.execute("SELECT COUNT(*)" + live_base + " AND LOWER(feedback)='done'", live_params).fetchone()[0]

        # Top Dispositions in Live Calls
        live_disposition_rows = con.execute("""
            SELECT COALESCE(call_disposition, 'Uncategorized') as disp, COUNT(*) as cnt
            """ + live_base + """
            GROUP BY call_disposition ORDER BY cnt DESC LIMIT 8
        """, live_params).fetchall()

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
        "active_pips": active_pips,
        "total_live_calls": total_live_calls,
        "inbound_live_calls": inbound_live_calls,
        "outbound_live_calls": outbound_live_calls,
        "done_live_feedback": done_live_feedback
    }

    chart_data = {
        "low_scores": low_score_count,
        "mid_scores": avg_score_count,
        "good_scores": good_score_count,
        "fatal_count": fatal_count,
        "non_fatal_count": max(0, total_audits - fatal_count),
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
        "dates": [r["dt"] for r in timeline_rows],
        "inbound_timeline": [r["in_cnt"] for r in timeline_rows],
        "outbound_timeline": [r["out_cnt"] for r in timeline_rows],
        "total_timeline": [r["total_cnt"] for r in timeline_rows],
        "timeline_details": [dict(r) for r in timeline_rows],
        "auditor_names": [r["auditor_name"] for r in auditor_stats],
        "auditor_counts": [r["cnt"] for r in auditor_stats],
        "auditor_details": [dict(r) for r in auditor_stats],
        "top_advisors": [r["advisor_name"] for r in top_advisors_query],
        "top_scores": [float(r["avg_score"] or 0) for r in top_advisors_query],
        "top_advisor_details": [dict(r) for r in top_advisors_query],
        "open_warnings": open_warnings,
        "in_progress_warnings": in_progress_warnings,
        "closed_warnings": closed_warnings,
        "active_pips": active_pips,
        "total_live_calls": total_live_calls,
        "live_dispositions": [r["disp"] for r in live_disposition_rows],
        "live_disp_counts": [r["cnt"] for r in live_disposition_rows],
        "live_disp_details": [dict(r) for r in live_disposition_rows],
        "latest_audit_date": [r["dt"] for r in timeline_rows][-1] if timeline_rows else None,
        "earliest_audit_date": [r["dt"] for r in timeline_rows][0] if timeline_rows else None
    }

    now_dt = datetime.now()
    if last_sync_time:
        try:
            if isinstance(last_sync_time, datetime):
                parsed_dt = last_sync_time
            else:
                clean_time_str = str(last_sync_time).replace("T", " ")[:19]
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
        "is_filtered": bool(dept != "All" or month or start or end or selected_auditor or advisor_query or score_bracket != "All" or fatal_status != "All")
    }

    return render_template("dashboard.html",
                           page="dashboard",
                           metrics=metrics,
                           chart_data=chart_data,
                           advisor_summary=advisor_summary,
                           auditors_list=auditors_list,
                           available_months=available_months,
                           selected_month=month,
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
    limit = request.args.get("limit", "1000").strip().lower()
    
    where = ["department=?"]
    params = [dept]
    curr_user = getattr(request, "current_user", None)
    if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
        where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
    if q:
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR auditor_name LIKE ? OR call_id LIKE ? OR fatal_category LIKE ? OR error_categories LIKE ?)")
        params += [f"%{q}%"] * 6
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)

    limit_clause = ""
    if limit != "all":
        try:
            limit_val = int(limit)
            limit_clause = f" LIMIT {limit_val}"
        except ValueError:
            limit_clause = " LIMIT 1000"

    sql = "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC" + limit_clause
    count_sql = "SELECT COUNT(*) as total FROM audits WHERE " + " AND ".join(where)
    with db() as con:
        total_count = con.execute(count_sql, params).fetchone()["total"]
        rows = con.execute(sql, params).fetchall()

    return render_template("audits_view.html",
                           page=f"{dept.lower()}_audits",
                           title=f"{dept} Call Audits",
                           subtitle=f"Displaying evaluated customer calls for {dept} operations",
                           fixed_dept=True,
                           rows=rows, total_count=total_count, limit=limit,
                           q=q, dept=dept, start=start, end=end)

@app.route("/live-calls")
@login_required
def live_calls_view():
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "All")
    auditor = request.args.get("auditor", "").strip()
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    limit = request.args.get("limit", "1000").strip().lower()
    
    where = ["1=1"]
    params = []
    curr_user = getattr(request, "current_user", None)
    if curr_user and curr_user["role"] in ("viewer", "advisor") and curr_user["advisor_id"]:
        where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        params.extend([curr_user["advisor_id"], f"%{curr_user['advisor_id']}%"])
    if dept in ("Inbound", "Outbound"):
        where.append("department=?")
        params.append(dept)
    if auditor:
        where.append("auditor_name=?")
        params.append(auditor)
    if q:
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR auditor_name LIKE ? OR call_id LIKE ? OR calling_no LIKE ? OR call_disposition LIKE ?)")
        params += [f"%{q}%"] * 6
    if start:
        where.append("call_date >= ?")
        params.append(start)
    if end:
        where.append("call_date <= ?")
        params.append(end)

    limit_clause = ""
    if limit != "all":
        try:
            limit_val = int(limit)
            limit_clause = f" LIMIT {limit_val}"
        except ValueError:
            limit_clause = " LIMIT 1000"

    sql = "SELECT * FROM live_calls WHERE " + " AND ".join(where) + " ORDER BY call_date DESC, id DESC" + limit_clause
    count_sql = "SELECT COUNT(*) as total FROM live_calls WHERE " + " AND ".join(where)
    
    with db() as con:
        total_count = con.execute(count_sql, params).fetchone()["total"]
        rows = con.execute(sql, params).fetchall()
        auditors_list = [r[0] for r in con.execute("SELECT DISTINCT auditor_name FROM live_calls WHERE auditor_name IS NOT NULL AND auditor_name != '' ORDER BY auditor_name").fetchall()]

    return render_template("live_calls.html",
                           page="live_calls",
                           rows=rows,
                           total_count=total_count,
                           auditors_list=auditors_list,
                           selected_auditor=auditor,
                           limit=limit,
                           q=q,
                           dept=dept,
                           start=start,
                           end=end)

@app.route("/fatal-calls")
@login_required
def fatal_calls():
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    limit = request.args.get("limit", "1000").strip().lower()
    
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
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR auditor_name LIKE ? OR call_id LIKE ? OR fatal_category LIKE ?)")
        params += [f"%{q}%"] * 5
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)

    limit_clause = ""
    if limit != "all":
        try:
            limit_val = int(limit)
            limit_clause = f" LIMIT {limit_val}"
        except ValueError:
            limit_clause = " LIMIT 1000"

    sql = "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC" + limit_clause
    count_sql = "SELECT COUNT(*) as total FROM audits WHERE " + " AND ".join(where)
    with db() as con:
        total_count = con.execute(count_sql, params).fetchone()["total"]
        rows = con.execute(sql, params).fetchall()

    return render_template("audits_view.html",
                           page="fatal_calls",
                           title="Fatal Call Register",
                           subtitle="Critical compliance, zero-tolerance, and policy breach calls (independent of Quality Score)",
                           rows=rows, total_count=total_count, limit=limit,
                           q=q, dept=dept, start=start, end=end)

@app.route("/score-reports")
@login_required
def score_reports():
    q = request.args.get("q", "").strip()
    dept = request.args.get("department", "All")
    score_band = request.args.get("score_band", "All")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    limit = request.args.get("limit", "1000").strip().lower()
    
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
        where.append("(advisor_name LIKE ? OR advisor_id LIKE ? OR auditor_name LIKE ? OR call_id LIKE ?)")
        params += [f"%{q}%"] * 4
    if start:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start)
    if end:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end)

    limit_clause = ""
    if limit != "all":
        try:
            limit_val = int(limit)
            limit_clause = f" LIMIT {limit_val}"
        except ValueError:
            limit_clause = " LIMIT 1000"

    sql = "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY quality_score ASC" + limit_clause
    count_sql = "SELECT COUNT(*) as total FROM audits WHERE " + " AND ".join(where)
    with db() as con:
        total_count = con.execute(count_sql, params).fetchone()["total"]
        rows = con.execute(sql, params).fetchall()

    return render_template("audits_view.html",
                           page="score_reports",
                           title="Quality Score Reports",
                           subtitle="Detailed score category breakdown (Low <80%, Average 80-90%, Good >90%)",
                           show_score_filter=True,
                           score_band=score_band,
                           rows=rows, total_count=total_count, limit=limit,
                           q=q, dept=dept, start=start, end=end)

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

# --- Routes: Mahavitaran Electricity Bill Calculator ---
@app.route("/calculator")
@login_required
def bill_calculator():
    """Mahavitaran Electricity Bill Calculator accessible to all authenticated users."""
    return render_template("calculator.html", page="calculator")

# --- Routes: Advisor 360 Search ---
@app.route("/advisor-search")
@app.route("/advisor/search")
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
        where = ["(advisor_id = ? OR advisor_name LIKE ? OR auditor_name LIKE ?)"]
        params = [emp_id, f"%{emp_id}%", f"%{emp_id}%"]
        
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
        counts = {k: len(v.get("rows", [])) for k, v in datasets.items()}
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
        dataset = datasets[key]

    clean_name = key.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("<", "lt").replace(">", "gt").replace("-", "_").replace("%", "pct")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    
    log_audit("EXPORT_REPORT", key, f"Format: {fmt}, Auditor: {auditor or 'All'}, Rows: {len(dataset.get('rows', []))}")
    
    if fmt == "csv":
        csv_bytes = export_service.dataset_to_csv_bytes(dataset)
        output = io.BytesIO(csv_bytes)
        return send_file(output, mimetype="text/csv", as_attachment=True, download_name=f"{clean_name}_{timestamp}.csv")
    else:
        output = export_service.dataframe_to_styled_excel(dataset, sheet_name=key)
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

        audits_ds = export_service.query_to_dataset(con, "SELECT * FROM audits WHERE " + " AND ".join(where) + " ORDER BY COALESCE(call_date, evaluation_date) DESC", params)
        warn_ds = export_service.query_to_dataset(con, "SELECT * FROM warnings WHERE (advisor_id=? OR advisor_name LIKE ?) ORDER BY issue_date DESC", (emp_id, f"%{emp_id}%"))

    output = export_service.generate_combined_excel({
        "Audited Calls": audits_ds,
        "Warnings and PIPs": warn_ds
    })
    log_audit("EXPORT_ADVISOR_PROFILE", emp_id, f"Exported individual advisor report ({len(audits_ds.get('rows', []))} calls)")
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

@app.route("/warnings/<int:warning_id>/delete", methods=["POST"])
@roles("admin")
def delete_warning(warning_id):
    with db() as con:
        w = con.execute("SELECT * FROM warnings WHERE id=?", (warning_id,)).fetchone()
        if not w:
            abort(404)
        con.execute("DELETE FROM warnings WHERE id=?", (warning_id,))
        # Also clean associated email history if desired or keep as audit
        con.execute("DELETE FROM email_history WHERE warning_id=?", (warning_id,))
    log_audit("DELETE_WARNING", f"#{warning_id}", f"Advisor: {w['advisor_name']}, Type: {w['warning_type']}")
    flash(f"Warning / PIP record #{warning_id} has been permanently removed.", "info")
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
        dup_handling = request.form.get("duplicate_handling", "skip")
        if not file or not file.filename:
            flash("Please choose an Excel (.xlsx, .xlsm) or CSV file.", "error")
        elif department not in ("Inbound", "Outbound"):
            flash("Please select either Inbound or Outbound department.", "error")
        else:
            try:
                filename, headers, data_rows = read_upload(file)
                if is_live_call_workbook(headers, data_rows):
                    result = import_live_calls(headers, data_rows, department, filename, request.current_user["username"], duplicate_handling=dup_handling)
                    flash(f"Live Calls ingested: {result['inserted']} inserted, {result['duplicates']} duplicates skipped, {result['rejected']} rejected.", "success")
                else:
                    result = import_dataframe(headers, data_rows, department, filename, request.current_user["username"], duplicate_handling=dup_handling)
                    flash(f"Audit Workbook ingested: {result['inserted']} inserted, {result['duplicates']} duplicates skipped, {result['rejected']} rejected.", "success")
            except Exception as exc:
                flash(f"Ingestion failed: {str(exc)}", "error")

    with db() as con:
        import_batches = con.execute("SELECT * FROM import_batches ORDER BY uploaded_at DESC LIMIT 50").fetchall()

    effective_max_mb = get_max_upload_mb()

    return render_template("upload.html",
                           page="upload",
                           result=result,
                           import_batches=import_batches,
                           import_enabled=import_enabled,
                           max_mb=effective_max_mb)

@app.route("/upload/sample-template")
@login_required
def download_sample_template():
    template_type = request.args.get("type", "audits").lower()
    fmt = request.args.get("format", "xlsx").lower()
    if template_type == "live_calls":
        filename = f"MSEDCL_Live_Calls_Template.{fmt}"
        sample_records = [
            {
                "Call ID": "MSEDCL-INB-LIVE-100001",
                "Date": "2026-10-01",
                "Advisor ID": "AIPLCC1153",
                "Advisor Name": "RISHABH MANOHAR PATHAK",
                "Calling No": "9637933169",
                "Call Disposition": "Billing Related (Reconnection)",
                "Call Observation": "Opening given as per script. Consumer called regarding power failure in some houses. Apology done on call. Complete probing done.",
                "Area of Improvement": "Advisor should personalize to consumer at least 1 time on call. Avoid dead air.",
                "Feedback": "Done",
                "Location": "Nagpur Inbound",
                "Auditor Name": "Priya Latare"
            },
            {
                "Call ID": "MSEDCL-INB-LIVE-100002",
                "Date": "2026-10-01",
                "Advisor ID": "AIPLCC1114",
                "Advisor Name": "AACHAL RUSHIPAL TEMBHARE",
                "Calling No": "8767548350",
                "Call Disposition": "Billing Related (Reconnection)",
                "Call Observation": "Opening given as per script. Advisor checked information and provided clear update on bill due date.",
                "Area of Improvement": "Advisor should complete probing on call.",
                "Feedback": "Done",
                "Location": "Nagpur Inbound",
                "Auditor Name": "Priya Latare"
            }
        ]
        if fmt == "csv":
            out = io.StringIO()
            headers = list(sample_records[0].keys())
            writer = csv.DictWriter(out, fieldnames=headers)
            writer.writeheader()
            for r in sample_records:
                writer.writerow(r)
            output = io.BytesIO(out.getvalue().encode("utf-8"))
            mimetype = "text/csv"
        else:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "Live Calls"
            headers = list(sample_records[0].keys())
            ws.append(headers)
            for r in sample_records:
                ws.append([r.get(h) for h in headers])
            export_service.apply_excel_styling(ws, "Live Calls")
            output = io.BytesIO()
            wb.save(output)
            output.seek(0)
            mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return send_file(output, mimetype=mimetype, as_attachment=True, download_name=filename)

    sample_records = [
        {
            "Call ID": "MSEDCL-INB-100201",
            "Call Date": "2026-10-15",
            "Evaluation Date": "2026-10-16",
            "Employee ID": "ADV-1042",
            "Advisor Name": "Rahul Suresh Patil",
            "Team Leader": "Pooja Deshmukh",
            "Auditor Name": "Akash Santoshwar",
            "Quality Score": 94,
            "Fatal Status": "No",
            "Fatal Category": "",
            "Area of Improvement": "Active listening and CRM documentation",
            "Call Observation": "Agent handled consumer billing inquiry with clear explanation of slab tariffs. Verification was complete as per script. Professional closing.",
            "Location": "Pune Central",
            "Call Audio URL": "https://storage.msedcl.in/recordings/call_100201.mp3"
        },
        {
            "Call ID": "MSEDCL-INB-100202",
            "Call Date": "2026-10-15",
            "Evaluation Date": "2026-10-16",
            "Employee ID": "ADV-1088",
            "Advisor Name": "Sneha Amit Shinde",
            "Team Leader": "Pooja Deshmukh",
            "Auditor Name": "Akash Santoshwar",
            "Quality Score": 78,
            "Fatal Status": "No",
            "Fatal Category": "",
            "Area of Improvement": "Hold procedure adherence, Avoid long dead air",
            "Call Observation": "Consumer inquired regarding meter replacement complaint status. Advisor kept consumer on hold for more than 90 seconds without refresh.",
            "Location": "Nagpur Urban",
            "Call Audio URL": ""
        },
        {
            "Call ID": "MSEDCL-INB-100203",
            "Call Date": "2026-10-16",
            "Evaluation Date": "2026-10-16",
            "Employee ID": "ADV-1105",
            "Advisor Name": "Vikram Ramesh Kulkarni",
            "Team Leader": "Sachin Gaikwad",
            "Auditor Name": "Priya Jadhav",
            "Quality Score": 0,
            "Fatal Status": "Yes",
            "Fatal Category": "Customer Disconnection / Dead Air",
            "Area of Improvement": "Zero tolerance on abrupt call disconnect",
            "Call Observation": "Advisor abruptly disconnected call while consumer was reporting outage concern. Critical compliance breach.",
            "Location": "Nashik Circle",
            "Call Audio URL": ""
        },
        {
            "Call ID": "MSEDCL-INB-100204",
            "Call Date": "2026-10-16",
            "Evaluation Date": "2026-10-17",
            "Employee ID": "ADV-1042",
            "Advisor Name": "Rahul Suresh Patil",
            "Team Leader": "Pooja Deshmukh",
            "Auditor Name": "Priya Jadhav",
            "Quality Score": 98,
            "Fatal Status": "No",
            "Fatal Category": "",
            "Area of Improvement": "",
            "Call Observation": "Excellent call ownership, immediate grievance registration, clear ticket ID provided to consumer with SLA duration.",
            "Location": "Pune Central",
            "Call Audio URL": ""
        }
    ]

    filename = f"MSEDCL_Quality_Audit_Demo_Template.{fmt}"

    if fmt == "csv":
        out = io.StringIO()
        headers = list(sample_records[0].keys())
        writer = csv.DictWriter(out, fieldnames=headers)
        writer.writeheader()
        for r in sample_records:
            writer.writerow(r)
        output = io.BytesIO(out.getvalue().encode("utf-8"))
        mimetype = "text/csv"
    else:
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Audit Data"
        headers = list(sample_records[0].keys())
        ws.append(headers)
        for r in sample_records:
            ws.append([r.get(h) for h in headers])
        export_service.apply_excel_styling(ws, "Audit Data")
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return send_file(output, mimetype=mimetype, as_attachment=True, download_name=filename)

@app.route("/upload/batch/<batch_id>/delete", methods=["POST"])
@roles("admin", "quality")
def delete_import_batch(batch_id):
    with db() as con:
        batch = con.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            flash("Import batch not found.", "error")
            return redirect(url_for("upload"))
        # Delete associated audits or live_calls and batch entry
        deleted_audits = con.execute("DELETE FROM audits WHERE import_batch=?", (batch_id,)).rowcount
        deleted_live = con.execute("DELETE FROM live_calls WHERE import_batch=?", (batch_id,)).rowcount
        con.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))
    
    total_del = deleted_audits + deleted_live
    log_audit("DELETE_IMPORT_BATCH", batch_id, f"File: {batch['source_file']}, Deleted {total_del} records")
    flash(f"Import batch '{batch['source_file']}' removed along with {total_del} associated records.", "success")
    return redirect(url_for("upload"))

@app.route("/upload/batch/<batch_id>/details", methods=["GET"])
@roles("admin", "quality", "manager")
def get_batch_details(batch_id):
    with db() as con:
        batch = con.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            return {"error": "Batch not found"}, 404
        
        batch_dict = dict(batch)
        report = {}
        if batch_dict.get("report_json"):
            try:
                report = json.loads(batch_dict["report_json"])
            except Exception:
                report = {}
        
        # Fetch all inserted records for this batch (from audits or live_calls)
        if report.get("type") == "live_calls":
            inserted_rows = con.execute(
                """SELECT id, department, call_id, call_date, advisor_id, advisor_name,
                          calling_no, call_disposition, observation, improvement, feedback,
                          location, auditor_name, imported_at
                   FROM live_calls WHERE import_batch=? ORDER BY id ASC""",
                (batch_id,)
            ).fetchall()
        else:
            inserted_rows = con.execute(
                """SELECT id, department, call_id, call_date, evaluation_date, advisor_id, advisor_name,
                          team_leader, auditor_name, quality_score, is_fatal, fatal_category,
                          error_categories, observation, location, audio_url, imported_at
                   FROM audits WHERE import_batch=? ORDER BY id ASC""",
                (batch_id,)
            ).fetchall()
        
        inserted_list = [dict(r) for r in inserted_rows]
        duplicate_list = report.get("duplicate_rows", [])
        rejected_list = report.get("rejected_rows", [])
        
        # If legacy report only had errors list, map it into rejected_list format
        if not rejected_list and report.get("errors"):
            rejected_list = [
                {"row": e.get("row"), "reason": e.get("reason"), "data": {"advisor_name": "", "quality_score": ""}}
                for e in report.get("errors", [])
            ]
            
        return {
            "batch": {
                "id": batch_dict["id"],
                "source_file": batch_dict["source_file"],
                "department": batch_dict["department"],
                "uploaded_at": str(batch_dict["uploaded_at"]),
                "uploaded_by": batch_dict["uploaded_by"],
                "rows_seen": batch_dict["rows_seen"],
                "rows_inserted": batch_dict["rows_inserted"],
                "duplicates": batch_dict["duplicates"],
                "rejected": batch_dict["rejected"]
            },
            "inserted": inserted_list,
            "duplicates": duplicate_list,
            "rejected": rejected_list
        }

@app.route("/upload/batch/<batch_id>/reinsert", methods=["POST"])
@roles("admin", "quality")
def reinsert_batch_record(batch_id):
    data = request.get_json(force=True, silent=True) or request.form.to_dict()
    source_type = data.get("source_type", "rejected") # "rejected" or "duplicate"
    item_index = data.get("item_index") # index in the report list
    
    advisor = str(data.get("advisor_name") or "").strip()
    score = parse_score(data.get("quality_score"))
    if not advisor:
        return {"success": False, "error": "Advisor Name is required."}, 400
    if score is None:
        return {"success": False, "error": "Valid Quality Score is required."}, 400
        
    with db() as con:
        batch = con.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            return {"success": False, "error": "Import batch not found."}, 404
        
        batch_dict = dict(batch)
        department = batch_dict["department"]
        filename = batch_dict["source_file"]
        
        fatal_raw = str(data.get("is_fatal") or "0")
        fatal_category = str(data.get("fatal_category") or "").strip()
        error_categories = str(data.get("error_categories") or "").strip()
        fatal = int(truthy_fatal(fatal_raw) or bool(fatal_category and "non fatal" not in norm(fatal_category)))
        if fatal and not fatal_category:
            fatal_category = "Fatal — category needs review"
            
        record = {
            "call_id": str(data.get("call_id") or "").strip() or None,
            "call_date": parse_date(data.get("call_date")),
            "evaluation_date": parse_date(data.get("evaluation_date")),
            "advisor_id": str(data.get("advisor_id") or "").strip() or None,
            "advisor_name": advisor,
            "team_leader": str(data.get("team_leader") or "").strip() or None,
            "auditor_name": str(data.get("auditor_name") or "").strip() or None,
            "quality_score": score,
            "is_fatal": fatal,
            "fatal_category": fatal_category or None,
            "error_categories": error_categories or None,
            "observation": str(data.get("observation") or "").strip() or None,
            "location": str(data.get("location") or "").strip() or None,
            "audio_url": str(data.get("audio_url") or "").strip() or None
        }
        
        now = datetime.now().isoformat(timespec="seconds")
        # Generate hash - if force re-inserting duplicate, ensure uniqueness if requested
        force_insert = str(data.get("force_insert", "false")).lower() in ("true", "1")
        row_hash = make_hash(department, record)
        if force_insert:
            row_hash = row_hash[:56] + secrets.token_hex(4)
            
        try:
            cur = con.execute("""INSERT INTO audits(department,call_id,call_date,evaluation_date,advisor_id,advisor_name,
            team_leader,auditor_name,quality_score,is_fatal,fatal_category,error_categories,observation,location,
            audio_url,source_file,import_batch,row_hash,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (department, record["call_id"], record["call_date"], record["evaluation_date"], record["advisor_id"],
             record["advisor_name"], record["team_leader"], record["auditor_name"], record["quality_score"],
             record["is_fatal"], record["fatal_category"], record["error_categories"], record["observation"],
             record["location"], record["audio_url"], filename, batch_id, row_hash, now))
        except DbIntegrityError as e:
            return {"success": False, "error": f"Record still duplicates an existing record in the database. Use Force Insert to bypass hash check."}, 409
        except Exception as e:
            return {"success": False, "error": f"Database error: {str(e)}"}, 500

        # Update batch report json and counts
        report = {}
        if batch_dict.get("report_json"):
            try:
                report = json.loads(batch_dict["report_json"])
            except Exception:
                report = {}
                
        new_inserted = batch_dict["rows_inserted"] + 1
        new_duplicates = batch_dict["duplicates"]
        new_rejected = batch_dict["rejected"]
        
        if source_type == "rejected":
            new_rejected = max(0, new_rejected - 1)
            rejected_rows = report.get("rejected_rows", [])
            if item_index is not None and str(item_index).isdigit():
                idx = int(item_index)
                if 0 <= idx < len(rejected_rows):
                    rejected_rows.pop(idx)
            report["rejected_rows"] = rejected_rows
        elif source_type == "duplicate":
            new_duplicates = max(0, new_duplicates - 1)
            dup_rows = report.get("duplicate_rows", [])
            if item_index is not None and str(item_index).isdigit():
                idx = int(item_index)
                if 0 <= idx < len(dup_rows):
                    dup_rows.pop(idx)
            report["duplicate_rows"] = dup_rows
            
        con.execute(
            """UPDATE import_batches
               SET rows_inserted=?, duplicates=?, rejected=?, report_json=?
               WHERE id=?""",
            (new_inserted, new_duplicates, new_rejected, json.dumps(report, default=str), batch_id)
        )

    log_audit("REINSERT_AUDIT", batch_id, f"Re-inserted audit for '{advisor}' ({department}) from {source_type}")
    return {
        "success": True,
        "message": f"Successfully validated and inserted audit record for '{advisor}'!",
        "stats": {
            "rows_inserted": new_inserted,
            "duplicates": new_duplicates,
            "rejected": new_rejected
        }
    }

@app.route("/upload/batch/<batch_id>/reinsert_bulk", methods=["POST"])
@roles("admin", "quality")
def reinsert_batch_bulk(batch_id):
    data = request.get_json(force=True, silent=True) or {}
    items = data.get("items", [])
    if not items:
        return {"success": False, "error": "No items provided for bulk insert."}, 400

    with db() as con:
        batch = con.execute("SELECT * FROM import_batches WHERE id=?", (batch_id,)).fetchone()
        if not batch:
            return {"success": False, "error": "Import batch not found."}, 404

        batch_dict = dict(batch)
        department = batch_dict["department"]
        filename = batch_dict["source_file"]
        now = datetime.now().isoformat(timespec="seconds")

        report = {}
        if batch_dict.get("report_json"):
            try:
                report = json.loads(batch_dict["report_json"])
            except Exception:
                report = {}

        inserted_count = 0
        indices_to_remove = set()

        for it in items:
            idx = it.get("index")
            d = it.get("data") or {}
            advisor = str(d.get("advisor_name") or "").strip()
            score = parse_score(d.get("quality_score"))
            if not advisor or score is None:
                continue

            fatal_raw = str(d.get("is_fatal") or "0")
            fatal_category = str(d.get("fatal_category") or "").strip()
            error_categories = str(d.get("error_categories") or "").strip()
            fatal = int(truthy_fatal(fatal_raw) or bool(fatal_category and "non fatal" not in norm(fatal_category)))
            if fatal and not fatal_category:
                fatal_category = "Fatal — category needs review"

            record = {
                "call_id": str(d.get("call_id") or "").strip() or None,
                "call_date": parse_date(d.get("call_date")),
                "evaluation_date": parse_date(d.get("evaluation_date")),
                "advisor_id": str(d.get("advisor_id") or "").strip() or None,
                "advisor_name": advisor,
                "team_leader": str(d.get("team_leader") or "").strip() or None,
                "auditor_name": str(d.get("auditor_name") or "").strip() or None,
                "quality_score": score,
                "is_fatal": fatal,
                "fatal_category": fatal_category or None,
                "error_categories": error_categories or None,
                "observation": str(d.get("observation") or "").strip() or None,
                "location": str(d.get("location") or "").strip() or None,
                "audio_url": str(d.get("audio_url") or "").strip() or None
            }

            # Force unique row_hash for bulk force-insert
            row_hash = make_hash(department, record)[:56] + secrets.token_hex(4)

            try:
                con.execute("""INSERT INTO audits(department,call_id,call_date,evaluation_date,advisor_id,advisor_name,
                team_leader,auditor_name,quality_score,is_fatal,fatal_category,error_categories,observation,location,
                audio_url,source_file,import_batch,row_hash,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (department, record["call_id"], record["call_date"], record["evaluation_date"], record["advisor_id"],
                 record["advisor_name"], record["team_leader"], record["auditor_name"], record["quality_score"],
                 record["is_fatal"], record["fatal_category"], record["error_categories"], record["observation"],
                 record["location"], record["audio_url"], filename, batch_id, row_hash, now))
                inserted_count += 1
                if idx is not None:
                    indices_to_remove.add(int(idx))
            except Exception as e:
                print(f"[WARN] Bulk re-insert row skip: {e}")

        # Update duplicate_rows in report
        dup_rows = report.get("duplicate_rows", [])
        updated_dup_rows = [row for i, row in enumerate(dup_rows) if i not in indices_to_remove]
        report["duplicate_rows"] = updated_dup_rows

        new_inserted = batch_dict["rows_inserted"] + inserted_count
        new_duplicates = max(0, batch_dict["duplicates"] - inserted_count)
        new_rejected = batch_dict["rejected"]

        con.execute(
            """UPDATE import_batches
               SET rows_inserted=?, duplicates=?, rejected=?, report_json=?
               WHERE id=?""",
            (new_inserted, new_duplicates, new_rejected, json.dumps(report, default=str), batch_id)
        )

    log_audit("BULK_REINSERT_AUDITS", batch_id, f"Bulk inserted {inserted_count} duplicates as new records")
    return {
        "success": True,
        "message": f"Successfully force-inserted {inserted_count} duplicate records as new audits!",
        "stats": {
            "rows_inserted": new_inserted,
            "duplicates": new_duplicates,
            "rejected": new_rejected
        }
    }

@app.route("/api/advisor/lookup")
@login_required
def advisor_lookup():
    q = request.args.get("query", "").strip()
    if not q or len(q) < 2:
        return {"found": False}
    with db() as con:
        # 1. Fetch advisor overall metrics
        r = con.execute("""
            SELECT advisor_id, advisor_name, department, team_leader,
                   COUNT(*) as total_calls,
                   SUM(is_fatal) as fatals,
                   SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_calls,
                   ROUND(AVG(quality_score), 2) as avg_score
            FROM audits
            WHERE advisor_id = ? OR advisor_name LIKE ?
            GROUP BY advisor_id, advisor_name, department, team_leader
            ORDER BY total_calls DESC LIMIT 1
        """, (q, f"%{q}%")).fetchone()

        if not r:
            return {"found": False}

        adv_id = r["advisor_id"] or ""
        adv_name = r["advisor_name"] or ""

        # 2. Check prior warning records in warnings table
        prior_warnings = con.execute("""
            SELECT id, warning_type, issue_date, status
            FROM warnings
            WHERE (advisor_id = ? AND advisor_id != '') OR advisor_name LIKE ?
            ORDER BY id ASC
        """, (adv_id, f"%{adv_name}%")).fetchall()
        prior_count = len(prior_warnings)

        # 3. Retrieve evaluated call interaction for evidence based on requested tier
        requested_tier = request.args.get("tier", "").strip()
        
        if requested_tier == "Appreciation Letter":
            latest_audit = con.execute("""
                SELECT call_id, call_date, evaluation_date, quality_score, is_fatal, fatal_category, error_categories, observation
                FROM audits
                WHERE ((advisor_id = ? AND advisor_id != '') OR advisor_name LIKE ?) AND is_fatal=0 AND quality_score >= 85
                ORDER BY id DESC LIMIT 1
            """, (adv_id, f"%{adv_name}%")).fetchone()
        elif requested_tier in ["First Warning", "Second Warning", "Final Warning", "Performance Improvement Plan (PIP)", "Coaching Notice"]:
            latest_audit = con.execute("""
                SELECT call_id, call_date, evaluation_date, quality_score, is_fatal, fatal_category, error_categories, observation
                FROM audits
                WHERE ((advisor_id = ? AND advisor_id != '') OR advisor_name LIKE ?) AND (is_fatal=1 OR quality_score < 80)
                ORDER BY id DESC LIMIT 1
            """, (adv_id, f"%{adv_name}%")).fetchone()
        else:
            latest_audit = con.execute("""
                SELECT call_id, call_date, evaluation_date, quality_score, is_fatal, fatal_category, error_categories, observation
                FROM audits
                WHERE ((advisor_id = ? AND advisor_id != '') OR advisor_name LIKE ?) AND (is_fatal=1 OR quality_score < 80)
                ORDER BY id DESC LIMIT 1
            """, (adv_id, f"%{adv_name}%")).fetchone()

        if not latest_audit:
            latest_audit = con.execute("""
                SELECT call_id, call_date, evaluation_date, quality_score, is_fatal, fatal_category, error_categories, observation
                FROM audits
                WHERE (advisor_id = ? AND advisor_id != '') OR advisor_name LIKE ?
                ORDER BY id DESC LIMIT 1
            """, (adv_id, f"%{adv_name}%")).fetchone()

    # 4. Determine status & tier
    call_date = (latest_audit["call_date"] if latest_audit and latest_audit["call_date"] else "Recent Evaluation")
    score = (latest_audit["quality_score"] if latest_audit and latest_audit["quality_score"] is not None else (r["avg_score"] or 0))
    is_fatal = bool(latest_audit and latest_audit["is_fatal"])
    is_fail = is_fatal or (score < 80)

    # Clean observation points
    obs_raw = (latest_audit["observation"] or "") if latest_audit else ""
    err_raw = (latest_audit["error_categories"] or "") if latest_audit else ""
    fatal_cat = (latest_audit["fatal_category"] or "Critical Failure") if latest_audit else ""

    bullets = []
    if is_fatal:
        bullets.append(f"Fatal Violation Identified: {fatal_cat}")
    if err_raw:
        for err_part in err_raw.split(","):
            part = err_part.strip().rstrip(".")
            if part and len(part) > 3:
                bullets.append(f"Error category noted: {part}")
    if obs_raw:
        sentences = [s.strip() for s in obs_raw.replace("\n", ". ").split(". ") if len(s.strip()) > 15]
        for s in sentences[:3]:
            bullets.append(s)
    if not bullets:
        bullets = ["Process deviation noted during standard call audit evaluation.", "Coaching required on active listening and accurate resolution."]

    bullet_text = "\n".join(f"- {b}" for b in bullets)

    email_prefix = (adv_id or adv_name.split()[0]).lower().replace(" ", ".")
    suggested_email = f"{email_prefix}@msedcl.in"

    # 5. Determine active tier: use requested_tier if provided, otherwise auto-calculate based on prior warnings and score
    if requested_tier:
        warning_type = requested_tier
    elif not is_fail and score >= 90:
        warning_type = "Appreciation Letter"
    elif prior_count == 0:
        warning_type = "First Warning"
    elif prior_count == 1:
        warning_type = "Second Warning"
    elif prior_count == 2:
        warning_type = "Final Warning"
    else:
        warning_type = "Performance Improvement Plan (PIP)"

    # Default review period in days based on tier
    review_days = 14
    if warning_type == "Performance Improvement Plan (PIP)":
        review_days = 30
    elif warning_type == "Final Warning":
        review_days = 7
    elif warning_type == "Second Warning":
        review_days = 14
    elif warning_type == "First Warning":
        review_days = 14
    elif warning_type == "Appreciation Letter":
        review_days = 90
    elif warning_type in ("Coaching Notice", "Quality Feedback Email"):
        review_days = 7

    # 6. Generate Tier-Specific Official QA Drafts & Subjects
    if warning_type == "Appreciation Letter":
        suggested_subject = f"Appreciation: Call Quality Evaluation - {adv_name} ({call_date})"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"The Quality Assurance Team would like to commend your excellent call handling observed during the evaluation of your customer interaction dated {call_date}.\n\n"
            f"Call Evaluation Summary:\n"
            f"- Advisor Name: {adv_name}\n"
            f"- Employee ID: {adv_id or 'N/A'}\n"
            f"- Department: {r['department'] or 'Inbound'}\n"
            f"- Evaluation Date: {call_date}\n"
            f"- Quality Score: {score}% (Pass / Exceeds Benchmark)\n\n"
            f"Key Observed Strengths:\n"
            f"- Courteous and standard opening/closing protocols.\n"
            f"- Active listening and swift comprehension of consumer concerns.\n"
            f"- Accurate guidance and first-contact problem resolution.\n"
            f"- Strict adherence to verification and SLA standards.\n\n"
            f"Your dedication to exceptional consumer service and quality excellence is greatly appreciated. Keep up the high standard of performance!\n\n"
            f"Best regards,\n"
            f"Quality Assurance Team\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    elif warning_type == "Second Warning":
        suggested_subject = f"Second Warning Letter: Continued Call Quality Deviation - {adv_name}"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"This email serves as your Second Formal Warning regarding continued non-compliance with MSEDCL call quality standards.\n\n"
            f"Despite previous coaching and formal notice, your call interaction evaluated on {call_date} demonstrates recurring quality gaps.\n\n"
            f"Call Evaluation Details:\n"
            f"- Advisor Name: {adv_name}\n"
            f"- Employee ID: {adv_id or 'N/A'}\n"
            f"- Department: {r['department'] or 'Inbound'}\n"
            f"- Evaluation Date: {call_date}\n"
            f"- Quality Score: {score}% (Fail)\n\n"
            f"Observed Deficiencies:\n"
            f"{bullet_text}\n\n"
            f"Urgent Mandatory Directives:\n"
            f"1. Follow standard verification protocols before disclosing account or billing information.\n"
            f"2. Strictly follow script guidelines and mandatory hold duration limits.\n"
            f"3. Ensure complete resolution notes are updated in CRM prior to call wrap-up.\n\n"
            f"Please note that continued failure to meet minimum benchmarks within the review timeline will result in immediate escalation to a Final Warning and formal disciplinary proceedings.\n\n"
            f"Regards,\n"
            f"Quality Assurance Team\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    elif warning_type == "Final Warning":
        suggested_subject = f"Final Warning Letter: Repeated Quality Standard Non-Compliance - {adv_name}"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"This communication serves as your FINAL OFFICIAL WARNING regarding serious and recurring quality deviations in call interactions.\n\n"
            f"Despite prior warning notices and structured coaching sessions, your call evaluated on {call_date} continues to fail mandatory quality and compliance requirements.\n\n"
            f"Call Evaluation Details:\n"
            f"- Advisor Name: {adv_name}\n"
            f"- Employee ID: {adv_id or 'N/A'}\n"
            f"- Department: {r['department'] or 'Inbound'}\n"
            f"- Evaluation Date: {call_date}\n"
            f"- Quality Score: {score}% (Critical Failure)\n\n"
            f"Non-Compliance Observations:\n"
            f"{bullet_text}\n\n"
            f"Critical Notice:\n"
            f"You are hereby given a final opportunity to rectify your performance. Any further failure or compliance breach will result in immediate placement on an escalated PIP or disciplinary action up to and including termination of service as per company policy.\n\n"
            f"Regards,\n"
            f"Quality Assurance & Operations Management\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    elif warning_type == "Performance Improvement Plan (PIP)":
        suggested_subject = f"Official Notice: Placement on Performance Improvement Plan (PIP) - {adv_name}"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"Following multiple quality audits and documented performance gaps, this serves as formal notification that you are placed on a structured Performance Improvement Plan (PIP).\n\n"
            f"Performance Background:\n"
            f"- Advisor Name: {adv_name} (ID: {adv_id or 'N/A'})\n"
            f"- Department: {r['department'] or 'Inbound'}\n"
            f"- Total Historical Audits: {r['total_calls']} (Avg Quality: {r['avg_score']}%)\n"
            f"- Most Recent Evaluation: {call_date} (Score: {score}%)\n"
            f"- Prior Documented Warnings: {prior_count}\n\n"
            f"Primary Deficiencies Identified:\n"
            f"{bullet_text}\n\n"
            f"PIP Targets & Mandatory Terms:\n"
            f"1. Maintain an average quality score of at least 85.0% across all evaluated interactions during the 30-day PIP period.\n"
            f"2. Zero fatal compliance breaches (customer verification, call disconnections, improper tariff advice).\n"
            f"3. Participate in mandatory weekly coaching check-ins with Quality & Team Leader ({r['team_leader'] or 'Supervisor'}).\n\n"
            f"Failure to meet these defined targets at the end of the PIP review period will lead to administrative termination of contract/employment.\n\n"
            f"Regards,\n"
            f"Human Resources & Quality Assurance Team\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    elif warning_type == "Coaching Notice":
        suggested_subject = f"Quality Coaching Notice: Targeted Skill Refinement - {adv_name}"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"During recent quality monitoring ({call_date}), specific areas were identified that require focused refresher training and coaching.\n\n"
            f"Evaluation Highlights:\n"
            f"- Advisor: {adv_name} (ID: {adv_id or 'N/A'})\n"
            f"- Quality Score: {score}%\n"
            f"- Department: {r['department'] or 'Inbound'}\n\n"
            f"Coaching Focus Areas:\n"
            f"{bullet_text}\n\n"
            f"Action Required:\n"
            f"Please schedule a 1-on-1 coaching session with your Quality Coach / Team Leader within 48 hours to review the recorded interaction and align on recommended call techniques.\n\n"
            f"Regards,\n"
            f"Quality Training & Development\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    elif warning_type == "Quality Feedback Email":
        suggested_subject = f"Quality Audit Feedback: Call Interaction dated {call_date} - {adv_name}"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"Please find below the quality evaluation summary for your customer call handled on {call_date}.\n\n"
            f"Audit Summary:\n"
            f"- Score: {score}%\n"
            f"- Call Date: {call_date}\n"
            f"- Department: {r['department'] or 'Inbound'}\n\n"
            f"Observations & Recommendations:\n"
            f"{bullet_text}\n\n"
            f"Please review the feedback and apply the suggested corrections in your upcoming interactions.\n\n"
            f"Regards,\n"
            f"Quality Assurance Team\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    elif warning_type == "Other Authorized Communication":
        suggested_subject = f"Official Quality Communication - {adv_name} ({call_date})"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"This official communication pertains to quality standards and operational compliance observed during call evaluations.\n\n"
            f"Details:\n"
            f"- Advisor Name: {adv_name} (ID: {adv_id or 'N/A'})\n"
            f"- Department: {r['department'] or 'Inbound'}\n"
            f"- Evaluated Score: {score}%\n\n"
            f"Key Notes:\n"
            f"{bullet_text}\n\n"
            f"Regards,\n"
            f"Quality Assurance Team\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )
    else:  # First Warning
        suggested_subject = f"First Warning Letter: Call Quality Performance Concern - {adv_name}"
        suggested_body = (
            f"Dear {adv_name},\n\n"
            f"This email serves as your First Warning Letter regarding performance concerns and deviation from quality parameters observed during call evaluation.\n\n"
            f"Call Evaluation Details:\n"
            f"- Employee Name: {adv_name}\n"
            f"- Employee ID: {adv_id or 'N/A'}\n"
            f"- Department: {r['department'] or 'Inbound'}\n"
            f"- Call Date: {call_date}\n"
            f"- Status: Fail (Score: {score}%)\n\n"
            f"Call Observation Summary:\n"
            f"During the interaction, the advisor attempted to assist the consumer; however, the following concerns were observed:\n"
            f"{bullet_text}\n\n"
            f"These observations indicate gaps in process adherence, communication clarity, and quality compliance.\n\n"
            f"Areas of Required Improvement:\n"
            f"- Follow the approved opening and closing scripts.\n"
            f"- Complete mandatory verification steps (consumer name and registered mobile number).\n"
            f"- Follow proper hold procedure and committed hold time.\n"
            f"- Provide accurate and clear information to the consumer.\n"
            f"- Maintain professional communication and call ownership.\n\n"
            f"Important Notice:\n"
            f"This is your First Official Warning. You are expected to demonstrate immediate improvement in call handling and quality parameters. Your upcoming calls will be closely monitored by the Quality Team. Continued deviation may result in further escalation as per company policy.\n\n"
            f"Regards,\n"
            f"Quality Assurance Team\n"
            f"Maharashtra State Electricity Distribution Co. Ltd. (MSEDCL)"
        )

    return {
        "found": True,
        "advisor_name": adv_name,
        "advisor_id": adv_id,
        "department": r["department"] or "Inbound",
        "warning_type": warning_type,
        "review_days": review_days,
        "suggested_email": suggested_email,
        "suggested_subject": suggested_subject,
        "suggested_body": suggested_body,
        "remarks": f"Prior Warnings: {prior_count}, Total Audits: {r['total_calls']}, Avg Score: {r['avg_score']}%, Fatals: {r['fatals'] or 0}"
    }

# --- Routes: Admin User Management ---
@app.route("/admin/users")
@roles("admin", "manager")
def user_management():
    with db() as con:
        users = con.execute("SELECT * FROM users ORDER BY id ASC").fetchall()
        # Fetch distinct audited advisors for autocomplete and linkage
        audited_advisors = con.execute("""
            SELECT DISTINCT advisor_id, advisor_name, department
            FROM audits
            WHERE advisor_name IS NOT NULL AND advisor_name != ''
            ORDER BY advisor_name ASC
        """).fetchall()
        
        # Calculate unlinked advisors without user logins
        user_adv_ids = set(u["advisor_id"] for u in users if u["advisor_id"])
        unlinked_count = sum(1 for a in audited_advisors if a["advisor_id"] and a["advisor_id"] not in user_adv_ids)

    return render_template("user_management.html", page="users", users=users, audited_advisors=audited_advisors, unlinked_count=unlinked_count)

@app.route("/admin/users/bulk-import", methods=["POST"])
@roles("admin", "manager")
def user_bulk_import():
    file = request.files.get("users_file")
    if not file or not file.filename:
        flash("Please select a CSV or Excel file to import.", "error")
        return redirect(url_for("user_management"))

    filename = secure_filename(file.filename).lower()
    created = 0
    skipped = 0
    errors = []

    try:
        raw_bytes = file.read()
        rows = []
        if filename.endswith(".csv"):
            text = raw_bytes.decode("utf-8", errors="replace")
            reader = csv.reader(io.StringIO(text))
            for r in reader:
                if any(cell.strip() for cell in r):
                    rows.append([c.strip() for c in r])
        elif filename.endswith((".xlsx", ".xls")):
            wb = openpyxl.load_workbook(io.BytesIO(raw_bytes), data_only=True)
            ws = wb.active
            for r in ws.iter_rows(values_only=True):
                if any(cell is not None and str(cell).strip() != "" for cell in r):
                    rows.append([str(c).strip() if c is not None else "" for c in r])
        else:
            flash("Invalid format. Please upload a .csv or .xlsx file.", "error")
            return redirect(url_for("user_management"))

        if not rows or len(rows) < 2:
            flash("The uploaded user roster has no data rows.", "error")
            return redirect(url_for("user_management"))

        headers = [str(c).strip().lower().replace(" ", "_") for c in rows[0]]
        dict_rows = []
        for r in rows[1:]:
            row_dict = {}
            for idx, h in enumerate(headers):
                val = r[idx] if idx < len(r) else ""
                row_dict[h] = val
            dict_rows.append(row_dict)

        with db() as con:
            existing_usernames = set(r[0] for r in con.execute("SELECT username FROM users").fetchall())

            for row in dict_rows:
                username = str(row.get("username", "")).strip()
                if not username or username.lower() == "nan" or username in existing_usernames:
                    skipped += 1
                    continue

                full_name = str(row.get("full_name", "")).strip()
                if full_name.lower() == "nan": full_name = ""
                email = str(row.get("email", "")).strip()
                if email.lower() == "nan": email = ""
                role = str(row.get("role", "advisor")).strip().lower()
                if role not in ("admin", "quality", "manager", "viewer", "advisor"):
                    role = "advisor"

                advisor_id = str(row.get("advisor_id", "")).strip()
                if advisor_id.lower() == "nan" or not advisor_id:
                    advisor_id = None

                department = str(row.get("department", "All")).strip()
                if department.lower() == "nan" or not department:
                    department = "All"

                location = str(row.get("location", "")).strip()
                if location.lower() == "nan": location = ""

                raw_pwd = str(row.get("temporary_password", "")).strip()
                if raw_pwd.lower() == "nan" or not raw_pwd:
                    raw_pwd = secrets.token_urlsafe(8)

                pwd_hash = generate_password_hash(raw_pwd)

                con.execute("""
                    INSERT INTO users (username, password_hash, role, advisor_id, active, full_name, email, department, location, created_at)
                    VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, datetime('now'))
                """, (username, pwd_hash, role, advisor_id, full_name, email, department, location))

                existing_usernames.add(username)
                created += 1

        log_audit("BULK_IMPORT_USERS", f"{created} accounts", f"Imported from {filename}. Skipped {skipped}.")
        flash(f"Successfully provisioned {created} user accounts! ({skipped} skipped/duplicates)", "success")
    except Exception as e:
        flash(f"Error parsing user import file: {str(e)}", "error")

    return redirect(url_for("user_management"))

@app.route("/admin/users/auto-provision-advisors", methods=["POST"])
@roles("admin")
def user_auto_provision_advisors():
    """Auto-generate advisor login accounts for all distinct audited advisors without accounts."""
    created_manifest = []

    with db() as con:
        existing_users = con.execute("SELECT username, advisor_id FROM users").fetchall()
        existing_adv_ids = set(u["advisor_id"] for u in existing_users if u["advisor_id"])
        existing_usernames = set(u["username"] for u in existing_users)

        audited_advisors = con.execute("""
            SELECT DISTINCT advisor_id, advisor_name, department
            FROM audits
            WHERE advisor_id IS NOT NULL AND advisor_id != ''
            ORDER BY advisor_name ASC
        """).fetchall()

        # Pre-hash once using pbkdf2 with low cost or standard hash to avoid 360 * 150ms delay
        default_pwd = "Msedcl@Password2026"
        shared_hash = generate_password_hash(default_pwd)

        batch_inserts = []
        for a in audited_advisors:
            adv_id = a["advisor_id"].strip()
            adv_name = a["advisor_name"].strip()
            dept = a["department"] or "All"

            if adv_id in existing_adv_ids:
                continue

            # Standardized username: adv.<cleaned_advisor_id>
            clean_id = adv_id.lower().replace(" ", "").replace("-", "")
            base_username = f"adv.{clean_id}"
            username = base_username
            ctr = 1
            while username in existing_usernames:
                username = f"{base_username}_{ctr}"
                ctr += 1

            email = f"{clean_id}@msedcl.in"

            batch_inserts.append((username, shared_hash, adv_id, adv_name, email, dept))
            existing_adv_ids.add(adv_id)
            existing_usernames.add(username)

            created_manifest.append({
                "username": username,
                "full_name": adv_name,
                "advisor_id": adv_id,
                "department": dept,
                "email": email,
                "initial_password": default_pwd
            })

        for row_vals in batch_inserts:
            con.execute("""
                INSERT INTO users (username, password_hash, role, advisor_id, active, full_name, email, department, location, created_at)
                VALUES (?, ?, 'advisor', ?, 1, ?, ?, ?, 'Contact Center', datetime('now'))
            """, row_vals)

    if not created_manifest:
        flash("All audited advisors already have user accounts.", "info")
        return redirect(url_for("user_management"))

    log_audit("AUTO_PROVISION_ADVISORS", f"{len(created_manifest)} accounts", "Auto-provisioned login accounts from audits table")

    # Generate CSV manifest for download
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=["username", "full_name", "advisor_id", "department", "email", "initial_password"])
    writer.writeheader()
    for row in created_manifest:
        writer.writerow(row)

    mem_bytes = io.BytesIO(output.getvalue().encode("utf-8"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(mem_bytes, mimetype="text/csv", as_attachment=True, download_name=f"msedcl_auto_provisioned_advisors_{timestamp}.csv")

@app.route("/admin/users/create", methods=["POST"])
@roles("admin", "manager")
def user_create():
    username = request.form.get("username", "").strip()
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "quality")
    advisor_id = request.form.get("advisor_id", "").strip() or None
    department = request.form.get("department", "All").strip()
    location = request.form.get("location", "").strip()
    password = request.form.get("password", "")

    if not username or not password:
        flash("Username and password are required.", "error")
        return redirect(url_for("user_management"))

    if role not in ("admin", "quality", "manager", "viewer", "advisor"):
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
                INSERT INTO users (username, password_hash, role, advisor_id, active, full_name, email, department, location, created_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, datetime('now'))
            """, (username, pwd_hash, role, advisor_id, full_name, email, department, location))
        log_audit("CREATE_USER", username, f"Role: {role}, AdvisorID: {advisor_id}, Dept: {department}, Email: {email}")
        flash(f"User '{username}' created successfully.", "success")
    except DbIntegrityError as e:
        flash(f"Could not create user: {e}", "error")

    return redirect(url_for("user_management"))

@app.route("/admin/users/<int:user_id>/update", methods=["POST"])
@roles("admin", "manager")
def user_update(user_id):
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "quality")
    advisor_id = request.form.get("advisor_id", "").strip() or None
    department = request.form.get("department", "All").strip()
    location = request.form.get("location", "").strip()

    if role not in ("admin", "quality", "manager", "viewer", "advisor"):
        flash("Invalid role selected.", "error")
        return redirect(url_for("user_management"))

    with db() as con:
        u = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            flash("User not found.", "error")
            return redirect(url_for("user_management"))

        con.execute("""
            UPDATE users
            SET full_name=?, email=?, role=?, advisor_id=?, department=?, location=?
            WHERE id=?
        """, (full_name, email, role, advisor_id, department, location, user_id))
        log_audit("UPDATE_USER", u["username"], f"Updated profile: Role={role}, AdvisorID={advisor_id}, Dept={department}")
    flash(f"Profile for '{u['username']}' updated successfully.", "success")
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
            ("ms_client_secret", request.form.get("ms_client_secret", "").strip()),
            # HR & Disciplinary Policy
            ("hr_email_cc", request.form.get("hr_email_cc", "").strip()),
            ("ops_escalation_email", request.form.get("ops_escalation_email", "").strip()),
            ("pip_duration_days", request.form.get("pip_duration_days", "30").strip()),
            ("warning_fatal_threshold", request.form.get("warning_fatal_threshold", "1").strip()),
            # Quality & Calibration Governance
            ("target_score_benchmark", request.form.get("target_score_benchmark", "85").strip()),
            ("excellence_score_threshold", request.form.get("excellence_score_threshold", "90").strip()),
            ("coaching_trigger_threshold", request.form.get("coaching_trigger_threshold", "80").strip()),
            ("calibration_variance_limit", request.form.get("calibration_variance_limit", "3.5").strip()),
            ("max_upload_size_mb", request.form.get("max_upload_size_mb", "500").strip())
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
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.accept_mimetypes.best == "application/json"
    
    if not recipient:
        if is_ajax:
            return jsonify({"success": False, "message": "Please enter a test recipient email."}), 400
        flash("Please enter a test recipient email.", "error")
        return redirect(url_for("settings_page"))
        
    host = get_setting("smtp_host") or os.environ.get("SMTP_HOST")
    port = int(get_setting("smtp_port") or os.environ.get("SMTP_PORT", "587"))
    username = get_setting("smtp_username") or os.environ.get("SMTP_USERNAME")
    password = get_setting("smtp_password") or os.environ.get("SMTP_PASSWORD")
    sender = get_setting("smtp_from") or os.environ.get("SMTP_FROM", username or "quality@msedcl.in")

    if not all([host, username, password]):
        err_msg = "Test failed: SMTP Host, Username, or Password missing in configuration."
        if is_ajax:
            return jsonify({"success": False, "message": err_msg}), 400
        flash(err_msg, "error")
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
        success_msg = f"Test email successfully accepted by {host} for {recipient}!"
        if is_ajax:
            return jsonify({"success": True, "message": success_msg})
        flash(success_msg, "success")
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)}"
        log_audit("EMAIL_TEST_FAILED", recipient, err)
        if is_ajax:
            return jsonify({"success": False, "message": f"Connection test failed ({err})."}), 500
        flash(f"Email connection test failed ({err}).", "error")

    return redirect(url_for("settings_page"))

@app.route("/admin/backup/download")
@roles("admin")
def db_backup_download():
    fmt = request.args.get("format", "sql").lower()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if fmt == "csv" or fmt == "zip":
        import io, zipfile, csv
        mem_zip = io.BytesIO()
        tables = ['system_settings', 'users', 'warnings', 'email_history', 'audit_logs', 'audits']
        with zipfile.ZipFile(mem_zip, mode='w', compression=zipfile.ZIP_DEFLATED) as zf:
            with db() as con:
                for t in tables:
                    try:
                        rows = con.execute(f"SELECT * FROM {t}").fetchall()
                        if rows:
                            out = io.StringIO()
                            writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                            writer.writeheader()
                            for r in rows:
                                writer.writerow(dict(r))
                            zf.writestr(f"msedcl_{t}.csv", out.getvalue())
                    except Exception as e:
                        pass
        mem_zip.seek(0)
        log_audit("DOWNLOAD_CSV_BACKUP", "all_tables.zip", "All tables CSV zip backup downloaded")
        return send_file(mem_zip, mimetype="application/zip", as_attachment=True, download_name=f"msedcl_tables_backup_{timestamp}.zip")

    elif fmt == "sqlite" and os.path.exists(DB_PATH):
        log_audit("DOWNLOAD_SQLITE_BACKUP", "quality_audit.db", "SQLite database backup downloaded")
        return send_file(DB_PATH, as_attachment=True, download_name=f"quality_audit_backup_{timestamp}.db")

    else:
        # Default: Full SQL Dump
        import io
        mem_sql = io.StringIO()
        mem_sql.write(f"-- ========================================================\n")
        mem_sql.write(f"-- MSEDCL Quality Audit System Database Dump\n")
        mem_sql.write(f"-- Generated At: {datetime.now().isoformat()}\n")
        mem_sql.write(f"-- Platform: PostgreSQL / Compatible SQL\n")
        mem_sql.write(f"-- ========================================================\n\n")

        tables = ['system_settings', 'users', 'warnings', 'email_history', 'audit_logs', 'audits']
        with db() as con:
            for t in tables:
                try:
                    rows = con.execute(f"SELECT * FROM {t}").fetchall()
                    mem_sql.write(f"-- --------------------------------------------------------\n")
                    mem_sql.write(f"-- Table: {t} ({len(rows)} records)\n")
                    mem_sql.write(f"-- --------------------------------------------------------\n")
                    if rows:
                        cols = list(rows[0].keys())
                        cols_str = ', '.join([f'"{c}"' for c in cols])
                        for r in rows:
                            vals = []
                            for c in cols:
                                v = r[c]
                                if v is None:
                                    vals.append('NULL')
                                elif isinstance(v, (int, float)):
                                    vals.append(str(v))
                                else:
                                    escaped = str(v).replace("'", "''")
                                    vals.append(f"'{escaped}'")
                            mem_sql.write(f"INSERT INTO {t} ({cols_str}) VALUES ({', '.join(vals)});\n")
                    mem_sql.write('\n')
                except Exception as e:
                    mem_sql.write(f"-- Error dumping {t}: {e}\n\n")

        mem_bytes = io.BytesIO(mem_sql.getvalue().encode('utf-8'))
        log_audit("DOWNLOAD_SQL_BACKUP", "msedcl_dump.sql", "Full SQL relational backup downloaded")
        return send_file(mem_bytes, mimetype="application/sql", as_attachment=True, download_name=f"msedcl_full_backup_{timestamp}.sql")

@app.route("/admin/audit-logs")
@roles("admin")
def audit_logs():
    with db() as con:
        logs = con.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("audit_logs.html", page="audit_logs", logs=logs)

# --- Admin Database Explorer Routes ---
@app.route("/admin/database-explorer")
@roles("admin")
def db_explorer_view():
    overview = db_explorer_service.get_db_overview()
    active_table = request.args.get("table", "").strip()
    active_schema = request.args.get("schema", "public").strip()
    search = request.args.get("search", "").strip()
    filter_col = request.args.get("filter_col", "").strip() or None
    filter_val = request.args.get("filter_val", "").strip()
    sort_col = request.args.get("sort", "").strip() or None
    sort_dir = request.args.get("dir", "ASC").strip()
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    # Default to first table if none selected
    if not active_table and overview.get("tables_summary"):
        first_tbl = overview["tables_summary"][0]
        active_table = first_tbl["name"]
        active_schema = first_tbl["schema"]

    records_data = None
    if active_table:
        try:
            records_data = db_explorer_service.get_table_records(
                table_name=active_table,
                schema_name=active_schema,
                page=page,
                per_page=per_page,
                search=search,
                sort_col=sort_col,
                sort_dir=sort_dir,
                filter_col=filter_col,
                filter_val=filter_val
            )
        except Exception as e:
            flash(f"Error reading table '{active_table}': {str(e)}", "error")

    log_audit("VIEW_DB_EXPLORER", active_table or "overview", f"Schema: {active_schema}")
    return render_template(
        "db_explorer.html",
        page="db_explorer",
        overview=overview,
        active_table=active_table,
        records_data=records_data
    )

@app.route("/admin/database-explorer/export-csv")
@roles("admin")
def db_explorer_export_csv():
    table = request.args.get("table", "").strip()
    schema = request.args.get("schema", "public").strip()
    search = request.args.get("search", "").strip()
    filter_col = request.args.get("filter_col", "").strip() or None
    filter_val = request.args.get("filter_val", "").strip()

    if not table:
        abort(400, "Table name required")

    try:
        csv_data = db_explorer_service.export_table_csv(table_name=table, schema_name=schema, search=search, filter_col=filter_col, filter_val=filter_val)
        log_audit("EXPORT_TABLE_CSV", table, f"Exported CSV for schema: {schema}")
        clean_filename = f"{table}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        return Response(
            csv_data,
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={clean_filename}"}
        )
    except Exception as e:
        flash(f"Failed to export CSV: {str(e)}", "error")
        return redirect(url_for("db_explorer_view", table=table, schema=schema))


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

# --- Notification Management & Dispatch Center ---
@app.route("/notifications")
@login_required
def notifications_view():
    curr_user = request.current_user
    with db() as con:
        # Fetch notifications targeted to user, role, or broadcast to all
        notifications = con.execute("""
            SELECT id, user_id, target_role, title, message, notif_type, link_url, created_at, is_read, created_by
            FROM notifications
            WHERE (user_id = ? OR target_role = ? OR target_role = 'all' OR (target_role IS NULL AND user_id IS NULL))
            ORDER BY id DESC LIMIT 100
        """, (curr_user["id"], curr_user["role"])).fetchall()

        unread_cnt = sum(1 for n in notifications if not n["is_read"])

        # For admins/managers/quality, fetch list of active users to send notifications to
        all_users = []
        if curr_user["role"] in ("admin", "manager", "quality"):
            all_users = con.execute("""
                SELECT id, username, full_name, role, advisor_id 
                FROM users 
                WHERE active = 1 
                ORDER BY role ASC, full_name ASC, username ASC
            """).fetchall()

    return render_template(
        "notifications.html",
        page="notifications",
        notifications=notifications,
        unread_count=unread_cnt,
        all_users=all_users
    )

@app.route("/notifications/send", methods=["POST"])
@roles("admin", "manager", "quality")
def notification_send():
    curr_user = request.current_user
    title = request.form.get("title", "").strip()
    message = request.form.get("message", "").strip()
    target_scope = request.form.get("target_scope", "all").strip()
    notif_type = request.form.get("notif_type", "info").strip()
    link_url = request.form.get("link_url", "").strip() or None

    if not title or not message:
        flash("Notification title and message content are required.", "error")
        return redirect(url_for("notifications_view"))

    user_id = None
    target_role = None

    if target_scope == "all":
        target_role = "all"
    elif target_scope.startswith("role:"):
        target_role = target_scope.split(":", 1)[1]
    elif target_scope == "specific":
        specific_uid = request.form.get("specific_user_id")
        if not specific_uid:
            flash("Please choose a specific user recipient.", "error")
            return redirect(url_for("notifications_view"))
        user_id = int(specific_uid)
    else:
        target_role = "all"

    creator_name = (curr_user["full_name"] or curr_user["username"]) if "full_name" in curr_user.keys() else curr_user["username"]

    with db() as con:
        con.execute("""
            INSERT INTO notifications (user_id, target_role, title, message, notif_type, link_url, is_read, created_by)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?)
        """, (user_id, target_role, title, message, notif_type, link_url, creator_name))
        con.commit()

    audience_desc = "all users" if target_scope == "all" else (f"role: {target_role}" if target_role else f"user #{user_id}")
    log_audit("NOTIFICATION_SENT", "Notification", f"Notification '{title}' sent by {curr_user['username']} to {audience_desc}")
    flash(f"Notification successfully broadcasted to {audience_desc}!", "success")
    return redirect(url_for("notifications_view"))

@app.route("/notifications/<int:notif_id>/read", methods=["POST"])
@login_required
def notification_mark_read(notif_id):
    curr_user = request.current_user
    with db() as con:
        con.execute("""
            UPDATE notifications 
            SET is_read = 1 
            WHERE id = ? AND (user_id = ? OR target_role = ? OR target_role = 'all' OR (target_role IS NULL AND user_id IS NULL))
        """, (notif_id, curr_user["id"], curr_user["role"]))
        con.commit()
    return redirect(request.referrer or url_for("notifications_view"))

@app.route("/notifications/mark-all-read", methods=["POST"])
@login_required
def notifications_mark_all_read():
    curr_user = request.current_user
    with db() as con:
        con.execute("""
            UPDATE notifications 
            SET is_read = 1 
            WHERE is_read = 0 AND (user_id = ? OR target_role = ? OR target_role = 'all' OR (target_role IS NULL AND user_id IS NULL))
        """, (curr_user["id"], curr_user["role"]))
        con.commit()
    flash("All notifications marked as read.", "info")
    return redirect(request.referrer or url_for("notifications_view"))

@app.route("/notifications/<int:notif_id>/delete", methods=["POST"])
@roles("admin", "manager")
def notification_delete(notif_id):
    with db() as con:
        con.execute("DELETE FROM notifications WHERE id = ?", (notif_id,))
        con.commit()
    flash("Notification deleted successfully.", "info")
    return redirect(url_for("notifications_view"))

# --- Error Handlers ---
@app.errorhandler(403)
def forbidden(e):
    return "<h2 style='font-family:sans-serif;'>403 Forbidden: You do not possess authorized permissions to access this module.</h2>", 403

@app.errorhandler(404)
def not_found(e):
    return "<h2 style='font-family:sans-serif;'>404 Not Found: The requested resource does not exist.</h2>", 404

@app.errorhandler(413)
def too_large(e):
    limit_mb = get_max_upload_mb()
    return f"<h2 style='font-family:sans-serif;'>413 Request Entity Too Large: File upload exceeds maximum limit ({limit_mb} MB).</h2>", 413

@app.errorhandler(500)
def internal_error(e):
    import traceback
    orig_error = getattr(e, "original_exception", e)
    err_trace = traceback.format_exc()
    print(f"[ERROR 500] Internal Server Error: {orig_error}\n{err_trace}", file=sys.stderr)
    return """
    <div style="font-family:system-ui,-apple-system,sans-serif;max-width:600px;margin:60px auto;padding:32px;background:#fff;border-radius:12px;box-shadow:0 4px 12px rgba(0,0,0,0.08);border:1px solid #e2e8f0;">
      <div style="font-size:36px;margin-bottom:12px;">⚠️</div>
      <h2 style="color:#0f172a;margin:0 0 8px 0;font-size:20px;">500 · Internal Server Error</h2>
      <p style="color:#64748b;font-size:14px;line-height:1.5;margin-bottom:20px;">
        An unexpected error occurred while processing your request. The incident has been recorded in the application logs.
      </p>
      <div style="display:flex;gap:10px;">
        <a href="/" style="display:inline-block;padding:8px 16px;background:#0284c7;color:#fff;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;">Return to Dashboard</a>
        <a href="/login" style="display:inline-block;padding:8px 16px;background:#f1f5f9;color:#334155;text-decoration:none;border-radius:6px;font-size:13px;font-weight:600;">Sign In</a>
      </div>
    </div>
    """, 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
