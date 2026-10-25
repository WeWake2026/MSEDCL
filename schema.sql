-- ==============================================================================
-- PostgreSQL Schema for MSEDCL Quality Audit System
-- Production-ready schema with primary keys, sequences, foreign keys, and indexes
-- ==============================================================================

-- 1. Create Audits Table
CREATE TABLE IF NOT EXISTS audits (
    id SERIAL PRIMARY KEY,
    department VARCHAR(100) NOT NULL,
    call_id VARCHAR(150),
    call_date VARCHAR(50),
    evaluation_date VARCHAR(50),
    advisor_id VARCHAR(100),
    advisor_name VARCHAR(255) NOT NULL,
    team_leader VARCHAR(255),
    auditor_name VARCHAR(255),
    quality_score NUMERIC(5, 2),
    is_fatal SMALLINT NOT NULL DEFAULT 0,
    fatal_category VARCHAR(255),
    error_categories TEXT,
    observation TEXT,
    source_file VARCHAR(255),
    import_batch VARCHAR(100),
    row_hash VARCHAR(64) UNIQUE,
    imported_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    audio_url TEXT,
    location VARCHAR(255)
);

-- Indexes for Audits
CREATE INDEX IF NOT EXISTS idx_audits_department ON audits (department);
CREATE INDEX IF NOT EXISTS idx_audits_advisor_id ON audits (advisor_id);
CREATE INDEX IF NOT EXISTS idx_audits_advisor_name ON audits (advisor_name);
CREATE INDEX IF NOT EXISTS idx_audits_auditor_name ON audits (auditor_name);
CREATE INDEX IF NOT EXISTS idx_audits_call_date ON audits (call_date);
CREATE INDEX IF NOT EXISTS idx_audits_is_fatal ON audits (is_fatal);
CREATE INDEX IF NOT EXISTS idx_audits_row_hash ON audits (row_hash);

-- 2. Create Warnings & PIP Table
CREATE TABLE IF NOT EXISTS warnings (
    id SERIAL PRIMARY KEY,
    advisor_id VARCHAR(100),
    advisor_name VARCHAR(255) NOT NULL,
    warning_type VARCHAR(100) NOT NULL,
    issue_date VARCHAR(50) NOT NULL,
    recipient_email VARCHAR(255),
    subject VARCHAR(255),
    body TEXT,
    email_status VARCHAR(50) NOT NULL DEFAULT 'Draft',
    sent_at TIMESTAMP WITHOUT TIME ZONE,
    provider_message_id VARCHAR(255),
    related_audit_ids TEXT,
    created_by VARCHAR(100),
    remarks TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    department VARCHAR(100),
    fatal_count INTEGER DEFAULT 0,
    low_score_count INTEGER DEFAULT 0,
    avg_score NUMERIC(5, 2),
    issue_category VARCHAR(150),
    effective_date VARCHAR(50),
    review_date VARCHAR(50),
    approved_by VARCHAR(100),
    delivery_status VARCHAR(100) DEFAULT 'Unknown',
    acknowledgement_at TIMESTAMP WITHOUT TIME ZONE,
    status VARCHAR(50) DEFAULT 'Open',
    closure_date VARCHAR(50),
    closure_remarks TEXT,
    advisor_feedback TEXT,
    advisor_feedback_date TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_warnings_advisor_id ON warnings (advisor_id);
CREATE INDEX IF NOT EXISTS idx_warnings_status ON warnings (status);

-- 3. Create Import Batches Table
CREATE TABLE IF NOT EXISTS import_batches (
    id VARCHAR(100) PRIMARY KEY,
    department VARCHAR(100) NOT NULL,
    source_file VARCHAR(255) NOT NULL,
    uploaded_by VARCHAR(100) NOT NULL,
    uploaded_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    rows_seen INTEGER NOT NULL,
    rows_inserted INTEGER NOT NULL,
    duplicates INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    report_json TEXT
);

-- 4. Create Users Table
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(50) NOT NULL CHECK(role IN ('admin','quality','manager','viewer','advisor')),
    active SMALLINT NOT NULL DEFAULT 1,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP WITHOUT TIME ZONE,
    email VARCHAR(255),
    full_name VARCHAR(255),
    advisor_id VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);
CREATE INDEX IF NOT EXISTS idx_users_advisor_id ON users (advisor_id);

-- 5. Create Email History Table
CREATE TABLE IF NOT EXISTS email_history (
    id SERIAL PRIMARY KEY,
    warning_id INTEGER REFERENCES warnings(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    employee_id VARCHAR(100),
    advisor_name VARCHAR(255) NOT NULL,
    department VARCHAR(100),
    recipient_email VARCHAR(255) NOT NULL,
    email_type VARCHAR(100) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    related_audit_ids TEXT,
    created_by VARCHAR(100) NOT NULL,
    sent_status VARCHAR(50) NOT NULL DEFAULT 'Draft',
    sent_at TIMESTAMP WITHOUT TIME ZONE,
    delivery_status VARCHAR(100) DEFAULT 'Pending Confirmation',
    acknowledgement_at TIMESTAMP WITHOUT TIME ZONE,
    error_details TEXT
);

-- 6. Create Audit Logs Table
CREATE TABLE IF NOT EXISTS audit_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    actor_username VARCHAR(100) NOT NULL,
    actor_role VARCHAR(50) NOT NULL,
    action VARCHAR(100) NOT NULL,
    target_entity VARCHAR(255),
    details TEXT,
    ip_address VARCHAR(100)
);

CREATE INDEX IF NOT EXISTS idx_audit_logs_timestamp ON audit_logs (timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_logs_actor ON audit_logs (actor_username);

-- 7. Create System Settings Table
CREATE TABLE IF NOT EXISTS system_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_by VARCHAR(100) NOT NULL
);
