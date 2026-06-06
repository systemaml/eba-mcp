# Data Model — EBA MCP POC

This document reflects the implemented SQLite schema in `pipeline/eba_pipeline/index/schema.sql`.

## Tables

### `documents`

One row per EBA publication.

| Column | Type | Notes |
|---|---|---|
| `eba_id` | TEXT PRIMARY KEY | Official identifier, e.g. `EBA/GL/2021/02` |
| `title` | TEXT | Seed-document title |
| `document_type` | TEXT | e.g. `guidelines`, `opinion` |
| `topic` | TEXT | POC corpus uses `AML/CFT` |
| `language` | TEXT | Static `en` in the POC |
| `publication_url` | TEXT | EBA publication page URL |
| `published_at` | TEXT | ISO-like publication date from seed YAML |
| `applicability_status` | TEXT | Seed metadata, default `applicable` |
| `publication_status` | TEXT | Seed metadata, default `final` |

For production corpora, `publication_status` and `applicability_status` are central filtering fields. The default production corpus should contain current/applicable regulatory documents only; archive/proposed/draft records should be omitted or indexed in a separate non-default corpus.

### `document_versions`

One indexed version per document in the POC.

| Column | Type | Notes |
|---|---|---|
| `version_id` | INTEGER PRIMARY KEY | Internal version key |
| `document_id` | TEXT | FK to `documents.eba_id` |
| `version_label` | TEXT | POC value: `1.0` |
| `published_at` | TEXT | Publication date |
| `file_sha256` | TEXT | Present in schema; may be empty unless supplied by seed/index metadata |
| `file_path` | TEXT | Processed document directory path |
| `is_current` | INTEGER | `1` for indexed POC version |

### `document_relationships`

Schema exists for future M4 work. The POC does not populate this table.

### `chunks`

One row per parsed paragraph/section chunk.

| Column | Type | Notes |
|---|---|---|
| `chunk_id` | TEXT PRIMARY KEY | Deterministic chunk identifier |
| `document_version_id` | INTEGER | FK to `document_versions.version_id` |
| `language` | TEXT | `en` |
| `section_path` | TEXT | Best-effort heading context |
| `paragraph_ref` | TEXT NULL | Numbered paragraph reference, if detected |
| `page_start` | INTEGER | Source page start |
| `page_end` | INTEGER | Source page end |
| `text` | TEXT | Extracted chunk text |
| `text_hash` | TEXT | SHA256 prefix of chunk text |
| `chunk_type` | TEXT | `paragraph`, `heading`, `table`, `annex`, or `footnote` |
| `sequence_no` | INTEGER | Document-local order |

## Chunk ID format

Implemented format:

```text
{eba_id_slug}:{chunk_text_sha256_8}:{lang}:{type_initial}:{ref}:p{page_start}:s{sequence_no}
```

Example:

```text
EBA-GL-2021-02:5390a1ef:en:p:1:p12:s34
```

This is deterministic for the current parser, chunk text, source page, and document-local sequence. The page/sequence discriminator prevents repeated headings or table labels from colliding when the same text/ref appears on multiple pages.

## FTS5 index

`chunks_fts` is a contentless FTS5 table:

```sql
CREATE VIRTUAL TABLE chunks_fts USING fts5(
  chunk_id UNINDEXED,
  eba_id,
  title,
  section_path,
  paragraph_ref,
  body,
  topic,
  document_type,
  content=''
);
```

Because `content=''`, FTS columns are not read back directly at runtime. The index is populated with `rowid = chunks.rowid`; TypeScript search joins `chunks_fts.rowid` back to `chunks.rowid` to hydrate results.

Current ranking uses SQLite FTS5 `rank` ordering. The POC does not implement custom BM25 column weights or synonym expansion.

## `corpus_manifest`

Single-row table with build metadata:

| Column | Type | Notes |
|---|---|---|
| `manifest_hash` | TEXT | SHA256 over sorted chunk IDs |
| `built_at` | TEXT | UTC timestamp |
| `document_count` | INTEGER | Count from `documents` |
| `chunk_count` | INTEGER | Count from `chunks` |
| `embedding_model` | TEXT | Embedding model name, e.g. `nomic-embed-text` |
| `embedding_dim` | INTEGER | Embedding dimension, e.g. `768` |

## Vector index

The production corpus adds a sqlite-vec virtual table for semantic search:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
  embedding float[768]
);
```

`chunks_vec.rowid` maps 1:1 to `chunks.rowid`. At query time the retrieval engine performs a nearest-neighbour search against the query embedding and joins the rowid results back to `chunks` to hydrate full citation metadata.

Current production corpus: 42,146 vectors, `nomic-embed-text`, dim 768.

## Current corpus constraints

- English only.
- Semantic embedding vectors stored in `chunks_vec` (sqlite-vec); production corpus has 42,146 vectors, `nomic-embed-text`, dim 768.
- No relationship population (backlog).
- Full rebuild only; no incremental update model (backlog).
- Runtime uses SQLite/FTS5 + optional sqlite-vec extension for hybrid retrieval.
