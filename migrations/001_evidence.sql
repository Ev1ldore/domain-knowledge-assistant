-- Additive migration; legacy files/chunks/faq/history are retained.
CREATE TABLE IF NOT EXISTS evidence_records(kind TEXT NOT NULL, record_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(kind,record_id));
CREATE TABLE IF NOT EXISTS evidence_vectors(id TEXT PRIMARY KEY, file_id TEXT NOT NULL, name TEXT NOT NULL, text TEXT NOT NULL, metadata TEXT NOT NULL, vector TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS evidence_audit(id INTEGER PRIMARY KEY,actor TEXT NOT NULL,reason TEXT NOT NULL,kind TEXT NOT NULL,record_id TEXT NOT NULL,before_value TEXT,after_value TEXT,created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS evidence_audit_record ON evidence_audit(record_id,id);
