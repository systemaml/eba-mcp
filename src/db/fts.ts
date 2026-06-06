import Database from 'better-sqlite3';

import { addConsultationResponseExclusion, addTopicFilter } from './filter-helpers.js';
import { Chunk, SearchFilters } from './types.js';

const FTS5_RESERVED = new Set(['and', 'or', 'not']);

export function escapeFts(query: string): string {
  return query
    .replace(/[^\w\s]/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(t => t.length > 0 && !FTS5_RESERVED.has(t.toLowerCase()))
    .join(' AND ');
}

function buildOrFtsQuery(query: string): string {
  return query
    .replace(/[^\w\s]/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(t => t.length > 0)
    .join(' OR ');
}

export interface FtsResult extends Chunk {
  eba_id: string;
  title: string;
  rank: number;
}

function addFilter(
  conditions: string[],
  params: unknown[],
  filters: SearchFilters,
  key: keyof SearchFilters,
  column: string,
): void {
  const value = filters[key];
  if (value) {
    conditions.push(`${column} = ?`);
    params.push(value);
  }
}

export function ftsSearch(
  db: Database.Database,
  query: string,
  filters: SearchFilters = {},
  limit: number = 10
): FtsResult[] {
  const andQuery = escapeFts(query);
  if (!andQuery) return [];

  const orQuery = buildOrFtsQuery(query);

  const runSearch = (ftsQuery: string): FtsResult[] => {
    const conditions = ['chunks_fts MATCH ?'];
    const params: unknown[] = [ftsQuery];
    addFilter(conditions, params, filters, 'eba_id', 'd.eba_id');
    addFilter(conditions, params, filters, 'document_type', 'd.document_type');
    addTopicFilter(conditions, params, filters);
    addFilter(conditions, params, filters, 'publication_status', 'd.publication_status');
    addFilter(conditions, params, filters, 'applicability_status', 'd.applicability_status');
    addFilter(conditions, params, filters, 'language', 'c.language');
    addConsultationResponseExclusion(conditions, filters);
    params.push(limit);

    return db.prepare(`
        SELECT c.*, d.eba_id, d.title, f.rank AS rank
        FROM chunks_fts f
        JOIN chunks c ON f.rowid = c.rowid
        JOIN document_versions dv ON c.document_version_id = dv.version_id
        JOIN documents d ON dv.document_id = d.eba_id
        WHERE ${conditions.join(' AND ')}
        ORDER BY f.rank
        LIMIT ?
      `).all(...params) as FtsResult[];
  };

  const rows = runSearch(andQuery);
  if (rows.length > 0) return rows;

  if (!orQuery || orQuery === andQuery) return rows;

  return runSearch(orQuery);
}
