import Database from 'better-sqlite3';

import { EMBEDDING_MODEL, OLLAMA_TIMEOUT_MS, OLLAMA_URL, RRF_K, RRF_WEIGHT_FTS, RRF_WEIGHT_VEC } from '../config.js';
import { embedQuery } from './embed.js';
import { addConsultationResponseExclusion, addTopicFilter } from './filter-helpers.js';
import { FtsResult, ftsSearch } from './fts.js';
import { Chunk, SearchFilters } from './types.js';
import { VectorResult, vectorSearch } from './vector.js';

export interface HybridSearchResult extends Chunk {
  score: number;
  ftsRank?: number;
  vectorRank?: number;
  rank?: number;
  distance?: number;
}

export interface HybridSearchOutcome {
  results: HybridSearchResult[];
  search_mode: 'hybrid' | 'vector' | 'fts_fallback';
  embedding_model?: string;
  embeddings_available: boolean;
}

function filterVectorResults(
  db: Database.Database,
  results: VectorResult[],
  filters: SearchFilters,
): VectorResult[] {
  if (results.length === 0) {
    return results;
  }

  const conditions: string[] = [];
  const params: unknown[] = [];

  if (filters.eba_id) {
    conditions.push('d.eba_id = ?');
    params.push(filters.eba_id);
  }
  if (filters.document_type) {
    conditions.push('d.document_type = ?');
    params.push(filters.document_type);
  }
  addTopicFilter(conditions, params, filters);
  if (filters.publication_status) {
    conditions.push('d.publication_status = ?');
    params.push(filters.publication_status);
  }
  if (filters.applicability_status) {
    conditions.push('d.applicability_status = ?');
    params.push(filters.applicability_status);
  }
  if (filters.language) {
    conditions.push('c.language = ?');
    params.push(filters.language);
  }
  addConsultationResponseExclusion(conditions, filters);

  if (conditions.length === 0) {
    return results;
  }

  const placeholders = results.map(() => '?').join(', ');
  const matchingRows = db.prepare(`
      SELECT c.chunk_id
      FROM chunks c
      JOIN document_versions dv ON c.document_version_id = dv.version_id
      JOIN documents d ON dv.document_id = d.eba_id
      WHERE c.chunk_id IN (${placeholders})
        AND ${conditions.join(' AND ')}
    `).all(...results.map((result) => result.chunk_id), ...params) as Array<{ chunk_id: string }>;

  const allowedChunkIds = new Set(matchingRows.map((row) => row.chunk_id));
  return results.filter((result) => allowedChunkIds.has(result.chunk_id));
}

function addRankedResults(
  merged: Map<string, HybridSearchResult>,
  results: Array<FtsResult | VectorResult>,
  source: 'fts' | 'vector',
  weight: number,
): void {
  results.forEach((result, index) => {
    const rank = index + 1;
    const existing = merged.get(result.chunk_id) ?? {
      ...result,
      score: 0,
    };

    existing.score += weight / (RRF_K + rank);

    if (source === 'fts') {
      existing.ftsRank = rank;
      existing.rank = (result as FtsResult).rank;
    } else {
      existing.vectorRank = rank;
      existing.distance = (result as VectorResult).distance;
    }

    merged.set(result.chunk_id, existing);
  });
}

export async function hybridSearch(
  db: Database.Database,
  query: string,
  filters: SearchFilters = {},
  limit: number = 10,
): Promise<HybridSearchOutcome> {
  const trimmedQuery = query.trim();
  if (!trimmedQuery || limit <= 0) {
    return { results: [], search_mode: 'fts_fallback', embeddings_available: false };
  }

  const candidateLimit = Math.max(limit * 2, limit);
  const ftsResults = ftsSearch(db, trimmedQuery, filters, candidateLimit);

  let embedding: Float32Array;
  try {
    embedding = await embedQuery(trimmedQuery, {
      ollamaUrl: OLLAMA_URL,
      model: EMBEDDING_MODEL,
      timeoutMs: OLLAMA_TIMEOUT_MS,
    });
  } catch (error) {
    return {
      results: ftsSearch(db, trimmedQuery, filters, limit).map((result) => ({
        ...result,
        score: 0,
        ftsRank: result.rank,
      })),
      search_mode: 'fts_fallback',
      embeddings_available: false,
    };
  }

  let vectorResults: VectorResult[];
  try {
    vectorResults = filterVectorResults(db, vectorSearch(db, embedding, candidateLimit), filters);
  } catch (_error) {
    return {
      results: ftsSearch(db, trimmedQuery, filters, limit).map((result) => ({
        ...result,
        score: 0,
        ftsRank: result.rank,
      })),
      search_mode: 'fts_fallback',
      embeddings_available: false,
    };
  }

  const merged = new Map<string, HybridSearchResult>();
  addRankedResults(merged, ftsResults, 'fts', RRF_WEIGHT_FTS);
  addRankedResults(merged, vectorResults, 'vector', RRF_WEIGHT_VEC);

  const mergedResults = [...merged.values()]
    .sort((a, b) => {
      const scoreDelta = b.score - a.score;
      if (scoreDelta !== 0) {
        return scoreDelta;
      }

      const aFtsRank = a.ftsRank ?? Number.POSITIVE_INFINITY;
      const bFtsRank = b.ftsRank ?? Number.POSITIVE_INFINITY;
      if (aFtsRank !== bFtsRank) {
        return aFtsRank - bFtsRank;
      }

      const aVectorRank = a.vectorRank ?? Number.POSITIVE_INFINITY;
      const bVectorRank = b.vectorRank ?? Number.POSITIVE_INFINITY;
      if (aVectorRank !== bVectorRank) {
        return aVectorRank - bVectorRank;
      }

      return a.chunk_id.localeCompare(b.chunk_id);
    })
    .slice(0, limit);

  return {
    results: mergedResults,
    search_mode: 'hybrid',
    embedding_model: EMBEDDING_MODEL,
    embeddings_available: true,
  };
}

export async function vectorOnlySearch(
  db: Database.Database,
  query: string,
  filters: SearchFilters = {},
  limit: number = 10,
): Promise<HybridSearchOutcome> {
  const trimmedQuery = query.trim();
  if (!trimmedQuery || limit <= 0) {
    return { results: [], search_mode: 'fts_fallback', embeddings_available: false };
  }

  let embedding: Float32Array;
  try {
    embedding = await embedQuery(trimmedQuery, {
      ollamaUrl: OLLAMA_URL,
      model: EMBEDDING_MODEL,
      timeoutMs: OLLAMA_TIMEOUT_MS,
    });
  } catch (_error) {
    return {
      results: ftsSearch(db, trimmedQuery, filters, limit).map((result) => ({
        ...result,
        score: 0,
        ftsRank: result.rank,
      })),
      search_mode: 'fts_fallback',
      embeddings_available: false,
    };
  }

  let vectorResults: VectorResult[];
  try {
    vectorResults = filterVectorResults(db, vectorSearch(db, embedding, limit), filters);
  } catch (_error) {
    return {
      results: ftsSearch(db, trimmedQuery, filters, limit).map((result) => ({
        ...result,
        score: 0,
        ftsRank: result.rank,
      })),
      search_mode: 'fts_fallback',
      embeddings_available: false,
    };
  }

  return {
    results: vectorResults.map((result, index) => ({
      ...result,
      score: 1 / (index + 1),
      vectorRank: index + 1,
      distance: result.distance,
    })),
    search_mode: 'vector',
    embedding_model: EMBEDDING_MODEL,
    embeddings_available: true,
  };
}
