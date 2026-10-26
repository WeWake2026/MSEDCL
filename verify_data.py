import sqlite3
import database

def verify_reconciliation():
    sq = sqlite3.connect("quality_audit.db")
    sq_c = sq.cursor()
    pg = database.get_db()
    pg_c = pg.cursor()
    
    tables = [
        "audits",
        "users",
        "import_batches",
        "warnings",
        "email_history",
        "audit_logs",
        "system_settings",
        "notifications"
    ]
    
    print("=" * 60)
    print(f"{'TABLE NAME':<18} | {'SQLITE':<8} | {'POSTGRESQL':<10} | {'STATUS':<8}")
    print("=" * 60)
    
    all_ok = True
    for t in tables:
        sq_cnt = sq_c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        pg_cnt = pg_c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()["count"]
        status = "MATCH" if sq_cnt == pg_cnt else "MISMATCH"
        if sq_cnt != pg_cnt:
            all_ok = False
        print(f"{t:<18} | {sq_cnt:<8} | {pg_cnt:<10} | {status:<8}")
        
    print("=" * 60)
    print("Overall Reconciliation Status:", "PASS (ALL TABLES MATCH)" if all_ok else "FAIL")
    print("=" * 60)
    sq.close()
    pg.close()

if __name__ == "__main__":
    verify_reconciliation()
