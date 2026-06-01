# EBA Current Applicable Corpus

## Purpose

The production MCP corpus is intended for lawyers and compliance professionals who need citations to **currently applicable EBA regulatory material**. It is not intended to be a general archive search engine for every historical EBA file.

## Default Inclusion Policy

Include by default:

- EBA Guidelines.
- EBA Recommendations.
- Regulatory Technical Standards (RTS) where the publication is final/current and not merely a draft consultation artefact.
- Implementing Technical Standards (ITS) where the publication is final/current and not merely a draft consultation artefact.
- English canonical PDF files from `eba.europa.eu`.

## Default Exclusion Policy

Exclude by default:

- Consultation papers.
- Draft/proposed documents.
- Track-changes documents.
- Annex-only, instruction-only, mapping-only, template-like, or supporting files unless deliberately selected as canonical support material.
- Superseded, repealed, withdrawn, deprecated, historical, or archive-only publications.
- Non-PDF files.

## Discovery Profiles

`eba-pipeline discover` supports profiles:

- `current-applicable` — production-oriented default profile. It targets current/applicable regulatory material and applies exclusion heuristics for consultation, draft/proposed, track-changes, and annex-only artefacts.
- `broad` — stress-test/archive profile. It can include consultation papers, reports, opinions, decisions, and annual reports for parser and retrieval testing.

## Important Caveat

The `current-applicable` profile is a strong first-pass filter, not a final legal status engine. Production use still requires:

1. better official `eba_id` normalization,
2. lifecycle relationship extraction (`amends`, `repeals`, `supersedes`, `replaces`),
3. canonical document selection where consolidated versions exist,
4. review queue for uncertain documents,
5. explicit separation between production corpus and research/archive corpus.

## Expected Scale

The practical production corpus is expected to be far smaller than the full EBA archive:

- EBA current/applicable regulatory corpus: roughly **150–350 documents** after curation.
- Broader current regulatory corpus including more standards/support material: roughly **300–700 documents**.
- Full EBA archive/search corpus is intentionally out of scope for the default MCP server.
