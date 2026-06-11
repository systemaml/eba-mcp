import { randomUUID } from 'crypto';

import { getCorpusInfo } from '../db/retrieval.js';

export type Answerability = 'exact' | 'partial' | 'no_match' | 'error';

export interface McpResponse {
  answerability: Answerability;
  citations: unknown[];
  documents_considered?: string[];
  filters_applied?: Record<string, unknown>;
  embedding_model?: string;
  embeddings_available?: boolean;
  response_mode?: 'compact' | 'standard' | 'full';
  response_limited?: boolean;
  limit_reason?: 'citation_cap' | 'response_size_chars';
  available_citations?: number;
  returned_citations?: number;
  omitted_citations?: number;
  omitted_context?: number;
  response_size_chars?: number;
  response_size_budget_chars?: number;
  suggested_next_tools?: string[];
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
    embedding_model?: string;
    embeddings_available?: boolean;
    response_mode?: 'compact' | 'standard' | 'full';
    response_limited?: boolean;
    limit_reason?: 'citation_cap' | 'response_size_chars';
    available_citations?: number;
    returned_citations?: number;
    omitted_citations?: number;
    omitted_context?: number;
    response_size_chars?: number;
    response_size_budget_chars?: number;
    suggested_next_tools?: string[];
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
    embedding_model: options.embedding_model,
    embeddings_available: options.embeddings_available,
    response_mode: options.response_mode,
    response_limited: options.response_limited,
    limit_reason: options.limit_reason,
    available_citations: options.available_citations,
    returned_citations: options.returned_citations,
    omitted_citations: options.omitted_citations,
    omitted_context: options.omitted_context,
    response_size_chars: options.response_size_chars,
    response_size_budget_chars: options.response_size_budget_chars,
    suggested_next_tools: options.suggested_next_tools,
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
