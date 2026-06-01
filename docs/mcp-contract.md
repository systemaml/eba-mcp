# MCP Contract — EBA MCP POC

This document describes the **implemented POC runtime contract** for the TypeScript MCP server in `src/`.

Transport is stdio only. Runtime command (production corpus):

```bash
node dist/index.js --db data/corpora/eba-current-applicable-2026-06-01-nomic-embed-text.db
```

The server exposes exactly nine tools:

1. `eba_search`
2. `eba_get_document`
3. `eba_get_paragraph`
4. `eba_list_documents`
5. `eba_corpus_info`

The additional tools `eba_get_status`, `eba_get_versions`, `eba_validate_citation`, and `eba_diff_versions` are implemented below.

## Common response fields

Most tools return these fields:

```json
{
  "answerability": "exact | partial | no_match | error",
  "citations": [],
  "documents_considered": [],
  "filters_applied": {},
  "warnings": [],
  "query_trace_id": "uuid-v4",
  "corpus_version": "manifest-hash-prefix"
}
```

`eba_corpus_info` additionally returns `corpus_info`. `eba_list_documents` additionally returns `documents` and `total`.

## Citation object

Citation objects are produced by `src/citations/formatter.ts`:

```json
{
  "citation_id": "EBA-GL-2021-02:5390a1ef:en:p:1",
  "eba_id": "EBA/GL/2021/02",
  "paragraph_ref": "1",
  "section_path": "Guidelines",
  "page_start": 1,
  "page_end": 1,
  "text": "First 500 characters of chunk text...",
  "citation": "EBA/GL/2021/02, para. 1, p. 1",
  "chunk_type": "paragraph"
}
```

For chunks without a numbered paragraph, the citation string uses the section fallback:

```text
EBA/Op/2022/01, section "Executive Summary", p. 4
```

The POC does not expose `source_url`, `file_sha256`, `application_date`, or document status fields inside each citation object. Document-level metadata is available through `eba_list_documents` or `eba_get_document`.

## `eba_search`

Search EBA document chunks. Uses SQLite FTS5 keyword search by default (`fts_only`), or hybrid FTS5 + sqlite-vec cosine similarity (`hybrid`) when a vector-enabled DB and local Ollama are available. `EBA_SEARCH_MODE=auto` selects hybrid automatically when vectors are present.

### Input

```json
{
  "query": "customer due diligence",
  "filters": {
    "eba_id": "EBA/GL/2021/02",
    "document_type": "guidelines",
    "topic": "AML/CFT",
    "publication_status": "final",
    "applicability_status": "applicable",
    "language": "en"
  },
  "limit": 10,
  "include_context": false
}
```

All filters are applied in the FTS path. Exact `eba_id` lookup is supported when `query` itself is an EBA ID or when only `filters.eba_id` is provided.

`include_context: true` includes neighboring chunks around each hit in the returned citation list.

### Output

```json
{
  "answerability": "partial",
  "citations": [{ "citation": "EBA/GL/2021/02, para. 20.6, p. 135" }],
  "documents_considered": ["EBA/GL/2021/02"],
  "filters_applied": { "document_type": "guidelines" },
  "warnings": [],
  "query_trace_id": "...",
  "corpus_version": "cc75a91c1e091546"
}
```

## `eba_get_document`

Return document metadata and the first citation chunks for a specific EBA ID.

### Input

```json
{ "eba_id": "EBA/GL/2021/02", "language": "en" }
```

### Output

```json
{
  "answerability": "exact",
  "document": { "eba_id": "EBA/GL/2021/02", "title": "..." },
  "citations": []
}
```

## `eba_get_paragraph`

Return all chunks matching a paragraph reference in a document. Some source PDFs reuse paragraph-like numbers in tables/annexes; if multiple chunks match, all are returned in sequence order.

### Input

```json
{
  "eba_id": "EBA/GL/2021/02",
  "paragraph_ref": "1",
  "language": "en",
  "context_before": 1,
  "context_after": 1
}
```

Context bounds are integers from 0 to 3.

## `eba_list_documents`

List indexed documents with optional filters.

### Input

```json
{
  "filters": {
    "document_type": "guidelines",
    "topic": "AML/CFT",
    "publication_status": "final",
    "applicability_status": "applicable",
    "language": "en"
  },
  "limit": 20
}
```

### Output

```json
{
  "answerability": "partial",
  "documents": [],
  "total": 0,
  "filters_applied": {},
  "citations": [],
  "warnings": [],
  "query_trace_id": "...",
  "corpus_version": "cc75a91c1e091546"
}
```

## `eba_corpus_info`

Return corpus manifest data.

### Input

```json
{}
```

### Output

```json
{
  "answerability": "exact",
  "corpus_info": {
    "manifest_hash": "...",
    "built_at": "...",
    "document_count": 188,
    "chunk_count": 29952,
    "embedding_model": "nomic-embed-text",
    "embedding_dim": 768
  },
  "citations": [],
  "warnings": [],
  "query_trace_id": "...",
  "corpus_version": "ed8ded0b4649d5d6"
}
```

## `eba_get_status`

Return publication and applicability status metadata for a specific EBA document.

### Input

```json
{ "eba_id": "EBA/GL/2021/02" }
```

### Output

```json
{
  "answerability": "exact",
  "status": {
    "eba_id": "EBA/GL/2021/02",
    "publication_status": "final",
    "applicability_status": "applicable",
    "published_at": "...",
    "application_date": "...",
    "language": "en",
    "is_consultation": false,
    "is_superseded": false,
    "is_partially_superseded": false,
    "superseded_by": [],
    "amended_by": [],
    "warnings": []
  }
}
```

## `eba_get_versions`

Return the available versions for a specific EBA document.

### Input

```json
{ "eba_id": "EBA/GL/2021/02" }
```

### Output

```json
{
  "answerability": "exact",
  "versions": [
    {
      "version_label": "...",
      "published_at": "...",
      "is_current": true,
      "file_sha256": "..."
    }
  ]
}
```

## `eba_validate_citation`

Validate a citation chunk ID and return the related document status metadata.

### Input

```json
{ "chunk_id": "EBA-GL-2021-02:001921c3:en:p:seq-527" }
```

### Output

```json
{
  "answerability": "exact",
  "validation": {
    "valid": true,
    "chunk_exists": true,
    "document_eba_id": "EBA/GL/2021/02",
    "publication_status": "final",
    "applicability_status": "applicable",
    "is_superseded": false,
    "warnings": []
  }
}
```

## `eba_diff_versions`

Compare two versions of a specific EBA document.

### Input

```json
{ "eba_id": "EBA/GL/2021/02", "version_a": "v1", "version_b": "v2" }
```

### Output

```json
{
  "answerability": "exact",
  "diff": {
    "eba_id": "EBA/GL/2021/02",
    "version_a": "v1",
    "version_b": "v2",
    "changes": [
      { "field": "publication_status", "old_value": "draft", "new_value": "final" }
    ]
  }
}
```

## Validation rules

- `eba_id` must match `EBA/[TYPE]/YYYY/NN`.
- Only `language: "en"` is accepted in the POC.
- Unknown input keys are rejected.
- `limit` is bounded by each tool schema.
- Invalid input returns `answerability: "error"` through the MCP tool handler.

## Known limitations

- No HTTP/SSE transport (stdio only; Streamable HTTP planned for future milestone).
- Hybrid semantic search is active when `EBA_SEARCH_MODE=hybrid` or `auto` and a vector-enabled DB + local Ollama are available; FTS5 is always the fallback.
- `application_date` always returns `null` (column not yet in schema).
- Version history limited to single `1.0` entry per document (full version tracking planned for future milestone).
- Incremental index updates are not implemented; corpus updates require a full rebuild to a new versioned DB artifact.
