import { randomUUID } from 'crypto';

import { getCorpusInfo } from '../db/retrieval.js';

export type Answerability = 'exact' | 'partial' | 'no_match' | 'error';

export interface McpResponse {
  answerability: Answerability;
  citations: unknown[];
  documents_considered?: string[];
  filters_applied?: Record<string, unknown>;
  search_mode?: 'hybrid' | 'fts_fallback' | 'fts_only';
  warnings: string[];
  query_trace_id: string;
  corpus_version: string | null;
}

export function buildResponse(
  answerability: Answerability,
  citations: unknown[],
  options: {
    documents_considered?: string[];
    filters_applied?: Record<string, unknown>;
    search_mode?: 'hybrid' | 'fts_fallback' | 'fts_only';
    warnings?: string[];
    corpus_version?: string | null;
  } = {}
): McpResponse {
  let corpusVersion = options.corpus_version;
  if (corpusVersion === undefined) {
    try {
      const info = getCorpusInfo();
      corpusVersion = info?.manifest_hash?.slice(0, 16) ?? null;
    } catch (_error) {
      corpusVersion = null;
    }
  }
  return {
    answerability,
    citations,
    documents_considered: options.documents_considered,
    filters_applied: options.filters_applied,
    search_mode: options.search_mode,
    warnings: options.warnings || [],
    query_trace_id: randomUUID(),
    corpus_version: corpusVersion,
  };
}

export function determineAnswerability(count: number): Answerability {
  if (count === 0) return 'no_match';
  if (count === 1) return 'exact';
  return 'partial';
}
