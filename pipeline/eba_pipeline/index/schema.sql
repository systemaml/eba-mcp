PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
  eba_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  document_type TEXT NOT NULL,
  topic TEXT NOT NULL,
  language TEXT NOT NULL,
  publication_url TEXT NOT NULL,
  published_at TEXT,
  application_date TEXT,
  applicability_status TEXT NOT NULL,
  publication_status TEXT NOT NULL,
  is_canonical INTEGER NOT NULL DEFAULT 1 CHECK (is_canonical IN (0, 1))
);

CREATE TABLE IF NOT EXISTS document_versions (
  version_id INTEGER PRIMARY KEY AUTOINCREMENT,
  document_id TEXT NOT NULL REFERENCES documents(eba_id) ON DELETE CASCADE,
  version_label TEXT NOT NULL,
  published_at TEXT,
  file_sha256 TEXT NOT NULL,
  file_path TEXT NOT NULL,
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1))
);

CREATE TABLE IF NOT EXISTS document_relationships (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_eba_id TEXT NOT NULL,
  target_eba_id TEXT NOT NULL,
  relationship_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id TEXT PRIMARY KEY,
  document_version_id INTEGER NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
  language TEXT NOT NULL,
  section_path TEXT NOT NULL,
  paragraph_ref TEXT,
  page_start INTEGER,
  page_end INTEGER,
  section_ref TEXT,
  section_title TEXT,
  section_level INTEGER,
  parent_section_ref TEXT,
  document_region TEXT,
  metadata_confidence REAL,
  metadata_source TEXT,
  text TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  chunk_type TEXT NOT NULL,
  sequence_no INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS document_toc (
  document_version_id INTEGER NOT NULL REFERENCES document_versions(version_id) ON DELETE CASCADE,
  section_ref TEXT NOT NULL,
  title TEXT NOT NULL,
  level INTEGER NOT NULL,
  parent_section_ref TEXT,
  page_start INTEGER,
  page_end INTEGER,
  sequence_start INTEGER,
  sequence_end INTEGER,
  confidence REAL,
  source TEXT
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  chunk_id,
  eba_id,
  title,
  section_path,
  paragraph_ref,
  body,
  topic,
  document_type,
  content=''
);

CREATE TABLE IF NOT EXISTS corpus_manifest (
  manifest_hash TEXT PRIMARY KEY,
  built_at TEXT NOT NULL,
  document_count INTEGER NOT NULL,
  chunk_count INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_versions_document_id ON document_versions(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document_version_id ON chunks(document_version_id);
CREATE INDEX IF NOT EXISTS idx_chunks_section_path ON chunks(section_path);
CREATE INDEX IF NOT EXISTS idx_chunks_section_ref ON chunks(section_ref);
CREATE INDEX IF NOT EXISTS idx_document_toc_document_version_id ON document_toc(document_version_id);
CREATE INDEX IF NOT EXISTS idx_document_toc_section_ref ON document_toc(section_ref);
CREATE INDEX IF NOT EXISTS idx_document_toc_document_section_ref ON document_toc(document_version_id, section_ref);
