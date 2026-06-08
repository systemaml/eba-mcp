# MCP Contract — EBA MCP POC

This document describes the **implemented POC runtime contract** for the TypeScript MCP server in `src/`.

Transport is stdio only. Runtime command (production corpus):

```bash
node dist/index.js --db data/corpora/eba-corpus.db
```

The server exposes eleven tools:

1. `eba_search`
2. `eba_get_document`
3. `eba_get_paragraph`
4. `eba_get_section`
5. `eba_get_toc`
6. `eba_list_documents`
7. `eba_corpus_info`
8. `eba_get_status`
9. `eba_get_versions`
10. `eba_validate_citation`
11. `eba_diff_versions`

## Common response fields

Most tools return these fields:

```json
{
  "answerability": "exact | partial | no_match | error",
  "citations": [],
  "documents_considered": [],
  "filters_applied": {},
  "warnings": [],
  "response_mode": "standard",
  "response_limited": false,
  "available_citations": 10,
  "returned_citations": 10,
  "omitted_citations": 0,
  "omitted_context": 0,
  "response_size_chars": 12000,
  "response_size_budget_chars": 50000,
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
  "text": "Full chunk text or a bounded excerpt depending on the tool and max_chars...",
  "citation": "EBA/GL/2021/02, para. 1, p. 1",
  "chunk_type": "paragraph",
  "truncated": false,
  "truncation_offset": null
}
```

Field notes:

- `truncated` — always present; `true` when `text` was clipped by a tool-level `max_chars` value. For `eba_search`, this can come from either an explicit `max_chars` input or the implicit bounded default for the selected `response_mode`; for paragraph/document/section tools, omission of `max_chars` still returns full chunk text.
- `truncation_offset` — always present; `"M / N"` (chars shown / total chars) when `truncated` is `true`, otherwise `null`.
- `is_anchor?` — present **only** in `eba_get_paragraph` responses; `true` for the specifically requested paragraph, `false` for surrounding context chunks.
- `is_complete?` — present **only** in `eba_get_paragraph` responses; `false` when `chunk_id` ends in `:sub1` or `:sub2` (split paragraph fragment), `true` otherwise.

For chunks without a numbered paragraph, the citation string uses the section fallback:

```text
EBA/Op/2022/01, section "Executive Summary", p. 4
```

The POC does not expose `source_url`, `file_sha256`, or chunk-level document status fields inside each citation object. Document-level metadata including `application_date` is available through `eba_list_documents` or `eba_get_document`.

## `eba_search`

Search EBA document chunks. Retrieval defaults to hybrid FTS5 + sqlite-vec semantic search when a vector-enabled DB and local Ollama are available, and falls back to SQLite FTS5 when they are not. MCP clients may optionally set `search_mode` to force keyword-only, hybrid, or semantic-only retrieval. Queries should be in English; if the end user asks in Polish or another language, consumer agents should translate the search intent into focused English EBA regulatory terms before calling this tool.

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
    "language": "en",
    "exclude_consultation_responses": true
  },
  "limit": 10,
  "include_context": false,
  "max_citations": 10,
  "response_mode": "standard",
  "search_mode": "hybrid",
  "max_chars": 2000
}
```

All filters are applied in both FTS and hybrid paths. Exact `eba_id` lookup is supported when `query` itself is an EBA ID or when only `filters.eba_id` is provided.

`topic: "AML/CFT"` matches both documents explicitly tagged `AML/CFT` and AML-relevant document titles whose stored corpus topic is a publication facet such as `EBA guidelines` or `EBA opinion`. `exclude_consultation_responses` must be nested under `filters` and must be a JSON boolean (`true` or `false`), not the string `"true"`. Pass `true` to remove chunks in parsed feedback/consultation-response sections while leaving final guideline text searchable.

`eba_search` is intentionally bounded because it is a discovery tool, not a document dump. `limit` controls anchor search hits before context expansion. `max_citations` controls the final number of returned citation objects after optional context expansion. If omitted, `max_citations` defaults by `response_mode`: `compact=15`, `standard=10`, `full=5`.

`response_mode` controls detail level:

- `compact` — shorter discovery excerpts (default 600 chars when `max_chars` is omitted) and minimal citation fields.
- `standard` — default bounded citation-ready output (default 1200 chars when `max_chars` is omitted).
- `full` — longer excerpts for focused calls (default 5000 chars when `max_chars` is omitted), still subject to the response size budget.

`search_mode` is optional and defaults to `hybrid` preference:

- `hybrid` — use FTS5 + semantic vector fusion when vectors and Ollama are available; otherwise return FTS5 results with `search_mode: "fts_fallback"`.
- `fts` — force keyword-only FTS5 retrieval; responses report `search_mode: "fts_only"`.
- `vector` — force semantic-only vector retrieval when vectors and Ollama are available; otherwise return FTS5 results with `search_mode: "fts_fallback"`.

`include_context: true` includes neighboring chunks around each hit only within `max_citations` and the response budget. Use `eba_get_paragraph` or `eba_get_section` for full follow-up context.

### Output

```json
{
  "answerability": "partial",
  "citations": [{ "citation": "EBA/GL/2021/02, para. 20.6, p. 135" }],
  "documents_considered": ["EBA/GL/2021/02"],
  "filters_applied": { "document_type": "guidelines" },
  "search_mode": "hybrid",
  "embedding_model": "nomic-embed-text",
  "embeddings_available": true,
  "response_mode": "standard",
  "response_limited": false,
  "available_citations": 10,
  "returned_citations": 10,
  "omitted_citations": 0,
  "omitted_context": 0,
  "response_size_chars": 12000,
  "response_size_budget_chars": 50000,
  "warnings": [],
  "query_trace_id": "...",
  "corpus_version": "cc75a91c1e091546"
}
```

When the final citation cap or response-size budget is hit, `response_limited` is `true`, `limit_reason` is set to `citation_cap` or `response_size_chars`, and `warnings` explains how many citations/context chunks were omitted. `suggested_next_tools` points to focused retrieval tools for the omitted context.

For `eba_search`, `embeddings_available` reports whether semantic embedding retrieval actually ran for the query. When `search_mode` is `hybrid` or `vector`, `embedding_model` names the runtime model used for query-time embedding. If vector search is unavailable or the call requests `search_mode: "fts"`, `embeddings_available` is `false` and `embedding_model` is omitted.

## `eba_get_document`

Return document metadata and a small sample of leading citation chunks for a specific EBA ID. This is not a full-document dump; use `eba_get_toc` and `eba_get_section` to navigate and retrieve substantive sections.

### Input

```json
{ "eba_id": "EBA/GL/2021/02", "language": "en", "max_chars": 2000 }
```

### Output

```json
{
  "answerability": "exact",
  "document": { "eba_id": "EBA/GL/2021/02", "title": "...", "application_date": "2022-01-01" },
  "citations": [],
  "citation_sample": {
    "returned": 5,
    "max_returned": 5,
    "full_document_dump": false,
    "navigation_tools": ["eba_get_toc", "eba_get_section", "eba_get_paragraph"]
  }
}
```

## `eba_get_paragraph`

Return all chunks matching a paragraph reference in a document. Some source PDFs reuse paragraph-like numbers in tables/annexes; if multiple chunks match, all are returned in sequence order.

Accepts `paragraph_ref` (single reference) or `paragraph_refs` (batch of up to 20 references). At least one of the two must be provided.

### Input (single)

```json
{
  "eba_id": "EBA/GL/2021/02",
  "paragraph_ref": "1",
  "language": "en",
  "context_before": 1,
  "context_after": 1,
  "max_chars": 2000
}
```

### Input (batch)

```json
{
  "eba_id": "EBA/GL/2021/02",
  "paragraph_refs": ["1", "5", "10"],
  "language": "en",
  "context_before": 0,
  "context_after": 0,
  "max_chars": 2000
}
```

`paragraph_refs` accepts up to 20 paragraph references. `context_before` and `context_after` are integers from 0 to 10, allowing broader regulatory context around an exact paragraph. Omit `max_chars` to return full paragraph/chunk text; set it only when a bounded excerpt is needed.

### Output

All returned citations include `is_anchor` and `is_complete` flags:

```json
{
  "answerability": "exact",
  "citations": [
    {
      "citation_id": "...",
      "paragraph_ref": "1",
      "text": "...",
      "citation": "EBA/GL/2021/02, para. 1, p. 12",
      "truncated": false,
      "truncation_offset": null,
      "is_anchor": true,
      "is_complete": true
    },
    {
      "citation_id": "...",
      "paragraph_ref": "2",
      "text": "...",
      "citation": "EBA/GL/2021/02, para. 2, p. 12",
      "truncated": false,
      "truncation_offset": null,
      "is_anchor": false,
      "is_complete": true
    }
  ]
}
```

If an `eba_search` result has `paragraph_ref: null`, this tool cannot retrieve it by paragraph. Use `eba_get_section` for nearby section navigation or `eba_validate_citation` for the returned `citation_id`.

## `eba_get_section`

Return citation chunks for a numbered section or paragraph-prefix inside one document. For example, `section: "4"` matches `paragraph_ref` values `4`, `4.1`, `4.2`, etc., plus matching `section_path` headings. This is broad navigation rather than precision search: broad prefixes can include front matter, footnotes, consultation-response chunks, or many subsections. Use `eba_get_toc` first and choose the narrowest useful prefix; prefer `eba_get_paragraph` once exact paragraph references are known.

### Input

```json
{
  "eba_id": "EBA/GL/2021/02",
  "section": "4",
  "language": "en",
  "limit": 200,
  "max_chars": 2000
}
```

### Output

```json
{
  "answerability": "exact",
  "section": "4",
  "total_chunks": 25,
  "citations": [{ "citation": "EBA/GL/2021/02, para. 4.1, p. 18" }]
}
```

This is best-effort and depends on parsed `paragraph_ref` / `section_path` metadata.

## `eba_get_toc`

Return a best-effort outline for one document. The runtime normalizes noisy parser metadata, filters common front-matter/consultation boilerplate, and groups entries by numeric `section_ref` where possible. Entries are enriched with hierarchy level, parent reference, confidence, paragraph, page, and sequence ranges.

### Input

```json
{ "eba_id": "EBA/GL/2021/02", "language": "en", "limit": 200 }
```

### Output

```json
{
  "answerability": "exact",
  "toc": [
    {
      "section_path": "4. Customer due diligence",
      "section_ref": "4",
      "level": 1,
      "parent_section_ref": null,
      "confidence": "high",
      "paragraph_refs": ["4", "4.1", "4.2"],
      "first_paragraph_ref": "4",
      "last_paragraph_ref": "4.2",
      "page_start": 18,
      "page_end": 21,
      "first_sequence_no": 80,
      "last_sequence_no": 92,
      "chunk_count": 13
    }
  ],
  "total": 1
}
```

The outline is derived from parser metadata and is not guaranteed to match the printed PDF table of contents exactly.

## `eba_list_documents`

List indexed documents with optional filters. `topic="AML/CFT"` uses the same heuristic title expansion as `eba_search`: it matches documents explicitly tagged `AML/CFT` plus documents whose title contains AML-relevant keywords.

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
  "documents": [
    {
      "eba_id": "EBA/GL/2021/02",
      "title": "...",
      "document_type": "guidelines",
      "published_at": "2021-07-01",
      "application_date": "2022-01-01",
      "publication_status": "final",
      "applicability_status": "applicable",
      "is_canonical": true
    }
  ],
  "total": 1,
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
    "document_count": 346,
    "chunk_count": 42146,
    "embedding_model": "nomic-embed-text",
    "embedding_dim": 768,
    "server_capabilities": {
      "registered_tools": [
        "eba_search",
        "eba_get_document",
        "eba_get_paragraph",
        "eba_get_section",
        "eba_get_toc",
        "eba_list_documents",
        "eba_corpus_info",
        "eba_get_status",
        "eba_get_versions",
        "eba_validate_citation",
        "eba_diff_versions"
      ],
      "tool_count": 11
    }
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

Validate a returned citation identifier and return the related document status metadata. Prefer passing the `citation_id` field exactly as returned by citation-producing tools. `chunk_id` is accepted as a backward-compatible alias for the same value.

### Input

```json
{ "citation_id": "EBA-GL-2021-02:1633158a:en:p:3.6:p37:s114" }
```

Backward-compatible input:

```json
{ "chunk_id": "EBA-GL-2021-02:1633158a:en:p:3.6:p37:s114" }
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
- Hybrid semantic search is the default preference when a vector-enabled DB + local Ollama are available; FTS5 is the fallback when vector search is unavailable. `eba_search.search_mode` can request `hybrid`, `fts`, or `vector`; `EBA_SEARCH_MODE` remains an internal maintainer override for process defaults.
- `application_date` depends on successful pipeline metadata extraction and may be `null` for documents where no date was detected.
- Version history limited to single `1.0` entry per document (full version tracking planned for future milestone).
- Incremental index updates are not implemented; corpus updates require a full rebuild and a new GitHub Release artifact named `eba-corpus.db`.
