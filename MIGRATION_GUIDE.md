# Data Migration Guide: SQLite to PostgreSQL

This guide explains how to migrate all existing data (audits, users, warnings, import batches, logs, settings) from the embedded SQLite database (`quality_audit.db`) into **PostgreSQL**.

---

## 1. Migration Architecture & Safety Guarantees

1. **Source Integrity**: The source SQLite file is opened in read-only mode and is never modified during migration.
2. **Idempotent & Resumable**: All table inserts use `ON CONFLICT DO NOTHING` or `ON CONFLICT DO UPDATE` to prevent duplicate primary keys or repeated records.
3. **Sequence Synchronization**: Automatically aligns PostgreSQL `SERIAL` sequences to the highest existing `id` value.
4. **Dry-Run Validation**: Supports a `--dry-run` flag to count and reconcile rows without touching PostgreSQL.
5. **Reconciliation Audit**: Generates a structured `migration_report.json` reporting source counts, transferred counts, skipped rows, and status.

---

## 2. Pre-Migration Checklist

- [ ] Take a physical copy backup of `quality_audit.db`:
  ```bash
  copy quality_audit.db quality_audit_pre_migration.db
  ```
- [ ] Confirm PostgreSQL server is running and accessible.
- [ ] Confirm PostgreSQL schema is initialized using `python init_db.py` or `schema.sql`.

---

## 3. Step 1: Run Dry-Run Validation

Test the migration pipeline without executing database writes:
```bash
python migrate_sqlite_to_postgres.py --dry-run
```

Review the printed summary:
```text
Table 'audits': 1251 records to migrate.
Table 'users': 4 records to migrate.
Table 'warnings': 2 records to migrate.
Table 'import_batches': 3 records to migrate.
Table 'system_settings': 16 records to migrate.
Table 'audit_logs': 16 records to migrate.
Migration completed with status: SUCCESS
```

---

## 4. Step 2: Execute Live Migration

Run the migration script with your PostgreSQL connection string:
```bash
python migrate_sqlite_to_postgres.py --postgres-url "postgresql://msedcl_user:YourPassword@localhost:5432/msedcl_qa"
```

*Or simply ensure `DATABASE_URL` is set in `.env` and run:*
```bash
python migrate_sqlite_to_postgres.py
```

---

## 5. Step 3: Verification & Reconciliation

1. Check the generated reconciliation report:
   ```bash
   cat migration_report.json
   ```
2. Verify row counts in PostgreSQL:
   ```sql
   SELECT 'audits' AS tbl, COUNT(*) FROM audits
   UNION ALL
   SELECT 'users', COUNT(*) FROM users
   UNION ALL
   SELECT 'warnings', COUNT(*) FROM warnings
   UNION ALL
   SELECT 'import_batches', COUNT(*) FROM import_batches;
   ```
3. Test the web application against PostgreSQL:
   ```bash
   curl http://127.0.0.1:5000/healthz
   ```

---

## 6. Rollback Procedures

If any discrepancies arise during post-migration verification:
1. Revert `DATABASE_URL` in `.env` to empty (`DATABASE_URL=`).
2. The application will immediately switch back to `quality_audit.db`.
3. If PostgreSQL tables need to be cleared for a clean retry:
   ```sql
   TRUNCATE audits, warnings, email_history, import_batches, audit_logs CASCADE;
   ```
