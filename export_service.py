import io, zipfile, csv
from datetime import datetime
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

def apply_excel_styling(ws, title="Report"):
    # Header styling
    header_fill = PatternFill(start_color="1E3A8A", end_color="1E3A8A", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="Calibri", size=10)
    thin_border = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0')
    )
    
    # Freeze top row
    ws.freeze_panes = "A2"
    
    # Style header row
    for col_num in range(1, ws.max_column + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        
    # Style data rows and auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            if cell.row > 1:
                cell.font = data_font
                cell.border = thin_border
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 40)
        
    # Enable autofilter on the table range
    if ws.dimensions:
        ws.auto_filter.ref = ws.dimensions

def populate_sheet(ws, dataset, title="Data"):
    headers = dataset.get("headers", [])
    rows = dataset.get("rows", [])
    
    # Write headers
    ws.append(headers)
    
    # Write rows
    for r in rows:
        row_vals = []
        for h in headers:
            v = r.get(h) if isinstance(r, dict) else r
            row_vals.append(v)
        ws.append(row_vals)
        
    apply_excel_styling(ws, title)

def dataframe_to_styled_excel(dataset, sheet_name="Data"):
    output = io.BytesIO()
    wb = openpyxl.Workbook()
    ws = wb.active
    safe_name = sheet_name[:31]
    ws.title = safe_name
    populate_sheet(ws, dataset, safe_name)
    wb.save(output)
    output.seek(0)
    return output

def generate_combined_excel(sheets_dict):
    """
    sheets_dict: {'Sheet Name': {'headers': [...], 'rows': [...]}, ...}
    """
    output = io.BytesIO()
    wb = openpyxl.Workbook()
    # Remove default sheet
    default_sheet = wb.active
    
    for idx, (sheet_name, dataset) in enumerate(sheets_dict.items()):
        safe_name = sheet_name[:31]
        ws = wb.create_sheet(title=safe_name)
        populate_sheet(ws, dataset, safe_name)
        
    # Remove initial empty sheet
    if default_sheet in wb.worksheets and len(wb.worksheets) > 1:
        wb.remove(default_sheet)
        
    wb.save(output)
    output.seek(0)
    return output

def query_to_dataset(con, sql, params=()):
    cur = con.execute(sql, params)
    rows = cur.fetchall()
    if not rows:
        # extract description if available
        headers = [desc[0] for desc in cur.description] if cur.description else []
        return {"headers": headers, "rows": []}
    headers = list(rows[0].keys())
    return {
        "headers": headers,
        "rows": [dict(r) for r in rows]
    }

def build_export_datasets(con, start_date=None, end_date=None, department=None, auditor=None, current_advisor_id=None):
    where = ["1=1"]
    params = []
    
    if department and department in ("Inbound", "Outbound"):
        where.append("department = ?")
        params.append(department)
    if start_date:
        where.append("COALESCE(call_date, evaluation_date) >= ?")
        params.append(start_date)
    if end_date:
        where.append("COALESCE(call_date, evaluation_date) <= ?")
        params.append(end_date)
    if auditor:
        where.append("auditor_name = ?")
        params.append(auditor)
    if current_advisor_id:
        where.append("advisor_id = ?")
        params.append(current_advisor_id)
        
    base_where = " AND ".join(where)
    
    # 1. Inbound Audits
    inbound_ds = query_to_dataset(con, f"""
        SELECT call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, is_fatal, fatal_category, error_categories, observation, audio_url, source_file
        FROM audits WHERE department='Inbound' AND {base_where.replace('department = ?', '1=1')}
        ORDER BY COALESCE(call_date, evaluation_date) DESC
    """, [p for i, p in enumerate(params) if not (department and department in ('Inbound','Outbound') and params[i] == department)])

    # 1b. Live Monitored Calls
    live_where = ["1=1"]
    live_params = []
    if department in ("Inbound", "Outbound"):
        live_where.append("department = ?")
        live_params.append(department)
    if start_date:
        live_where.append("call_date >= ?")
        live_params.append(start_date)
    if end_date:
        live_where.append("call_date <= ?")
        live_params.append(end_date)
    if auditor:
        live_where.append("auditor_name = ?")
        live_params.append(auditor)
    if current_advisor_id:
        live_where.append("(advisor_id = ? OR advisor_name LIKE ?)")
        live_params.extend([current_advisor_id, f"%{current_advisor_id}%"])

    live_calls_ds = query_to_dataset(con, f"""
        SELECT department, call_id, call_date, advisor_id, advisor_name, calling_no,
               call_disposition, observation, improvement, feedback, location, auditor_name, source_file
        FROM live_calls WHERE {" AND ".join(live_where)}
        ORDER BY call_date DESC, id DESC
    """, live_params)
    
    # 2. Outbound Audits
    outbound_ds = query_to_dataset(con, f"""
        SELECT call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, is_fatal, fatal_category, error_categories, observation, audio_url, source_file
        FROM audits WHERE department='Outbound' AND {base_where.replace('department = ?', '1=1')}
        ORDER BY COALESCE(call_date, evaluation_date) DESC
    """, [p for i, p in enumerate(params) if not (department and department in ('Inbound','Outbound') and params[i] == department)])

    # 3. All Filtered Audits
    all_audits_ds = query_to_dataset(con, f"""
        SELECT department, call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, is_fatal, fatal_category, error_categories, observation, audio_url, source_file
        FROM audits WHERE {base_where}
        ORDER BY COALESCE(call_date, evaluation_date) DESC
    """, params)

    # 4. Fatal Calls
    fatal_ds = query_to_dataset(con, f"""
        SELECT department, call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, fatal_category, error_categories, observation, audio_url
        FROM audits WHERE is_fatal=1 AND {base_where}
        ORDER BY COALESCE(call_date, evaluation_date) DESC
    """, params)

    # 5. Low Score (<80%)
    low_ds = query_to_dataset(con, f"""
        SELECT department, call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, is_fatal, fatal_category, observation, audio_url
        FROM audits WHERE quality_score < 80 AND {base_where}
        ORDER BY quality_score ASC
    """, params)

    # 6. Average Score (80% - 90%)
    avg_ds = query_to_dataset(con, f"""
        SELECT department, call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, is_fatal, fatal_category, observation, audio_url
        FROM audits WHERE quality_score >= 80 AND quality_score <= 90 AND {base_where}
        ORDER BY quality_score ASC
    """, params)

    # 7. Good Calls (>90%)
    good_ds = query_to_dataset(con, f"""
        SELECT department, call_id, call_date, evaluation_date, advisor_id, advisor_name, team_leader,
               auditor_name, quality_score, is_fatal, fatal_category, observation, audio_url
        FROM audits WHERE quality_score > 90 AND {base_where}
        ORDER BY quality_score DESC
    """, params)

    # 8. Auditor Summary
    auditor_ds = query_to_dataset(con, f"""
        SELECT auditor_name, COUNT(*) as total_audits,
               ROUND(AVG(quality_score), 2) as average_score,
               SUM(is_fatal) as fatal_calls,
               SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_scores,
               SUM(CASE WHEN quality_score > 90 THEN 1 ELSE 0 END) as good_scores
        FROM audits WHERE {base_where} AND auditor_name IS NOT NULL
        GROUP BY auditor_name ORDER BY total_audits DESC
    """, params)

    # 9. Advisor Summary
    advisor_ds = query_to_dataset(con, f"""
        SELECT advisor_id, advisor_name, department, COUNT(*) as total_audits,
               ROUND(AVG(quality_score), 2) as average_score,
               SUM(is_fatal) as fatal_calls,
               SUM(CASE WHEN quality_score < 80 THEN 1 ELSE 0 END) as low_scores,
               SUM(CASE WHEN quality_score >= 80 AND quality_score <= 90 THEN 1 ELSE 0 END) as mid_scores,
               SUM(CASE WHEN quality_score > 90 THEN 1 ELSE 0 END) as good_scores,
               ROUND(SUM(is_fatal) * 100.0 / COUNT(*), 1) as fatal_rate_pct,
               CASE 
                   WHEN SUM(is_fatal) >= 2 OR AVG(quality_score) < 80 THEN 'Action Required (PIP/Warning)'
                   WHEN AVG(quality_score) >= 90 THEN 'Target Achieved (Green)'
                   ELSE 'In Coaching (Amber)'
               END as coaching_status
        FROM audits WHERE {base_where}
        GROUP BY advisor_id, advisor_name, department
        ORDER BY fatal_calls DESC, average_score ASC
    """, params)

    # 10. Warning & PIP History
    warnings_ds = query_to_dataset(con, """
        SELECT id, advisor_id, advisor_name, department, warning_type, issue_date, effective_date,
               review_date, recipient_email, subject, email_status, status, created_by, approved_by,
               closure_date, remarks
        FROM warnings ORDER BY id DESC
    """)

    # 11. Email History
    email_ds = query_to_dataset(con, """
        SELECT id, warning_id, created_at, employee_id, advisor_name, department, recipient_email,
               email_type, subject, sent_status, sent_at, delivery_status, error_details
        FROM email_history ORDER BY id DESC
    """)

    # 12. Import History
    imports_ds = query_to_dataset(con, """
        SELECT id as batch_id, department, source_file, uploaded_by, uploaded_at, rows_seen,
               rows_inserted, duplicates, rejected
        FROM import_batches ORDER BY uploaded_at DESC
    """)

    return {
        "Inbound Audits": inbound_ds,
        "Outbound Audits": outbound_ds,
        "Live Calls": live_calls_ds,
        "All Audits": all_audits_ds,
        "Fatal Calls": fatal_ds,
        "Low Score (<80%)": low_ds,
        "Average Score (80-90%)": avg_ds,
        "Good Calls (>90%)": good_ds,
        "Auditor Summary": auditor_ds,
        "Advisor Summary": advisor_ds,
        "Warning & PIP History": warnings_ds,
        "Email History": email_ds,
        "Import History": imports_ds
    }

def sanitize_value(val):
    if isinstance(val, str) and val and val[0] in ("=", "+", "-", "@", "\t", "\r"):
        return f"'{val}"
    return val

def dataset_to_csv_bytes(dataset):
    headers = dataset.get("headers", [])
    rows = dataset.get("rows", [])
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=headers)
    writer.writeheader()
    for r in rows:
        clean_row = {h: sanitize_value(r.get(h)) for h in headers}
        writer.writerow(clean_row)
    return out.getvalue().encode('utf-8')

def create_all_reports_zip(datasets):
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # Add individual CSVs and Excel files
        for name, dataset in datasets.items():
            clean_name = name.lower().replace(" ", "_").replace("(", "").replace(")", "").replace("<", "lt").replace(">", "gt").replace("-", "_").replace("%", "pct")
            
            # CSV
            csv_bytes = dataset_to_csv_bytes(dataset)
            zf.writestr(f"csv/{clean_name}.csv", csv_bytes)
            
            # Excel
            excel_bytes = dataframe_to_styled_excel(dataset, sheet_name=name).getvalue()
            zf.writestr(f"excel/{clean_name}.xlsx", excel_bytes)
            
        # Add Master Combined Workbook
        combined_bytes = generate_combined_excel({
            "Inbound Audits": datasets["Inbound Audits"],
            "Outbound Audits": datasets["Outbound Audits"],
            "Fatal Calls": datasets["Fatal Calls"],
            "Low Score Calls": datasets["Low Score (<80%)"],
            "Good Calls": datasets["Good Calls (>90%)"],
            "Auditor Summary": datasets["Auditor Summary"],
            "Advisor Summary": datasets["Advisor Summary"],
            "Warning and PIP History": datasets["Warning & PIP History"],
            "Email History": datasets["Email History"]
        }).getvalue()
        zf.writestr("MSEDCL_Quality_Audit_Combined_Master.xlsx", combined_bytes)
        
    zip_buffer.seek(0)
    return zip_buffer
