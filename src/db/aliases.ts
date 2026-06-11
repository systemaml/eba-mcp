import { SearchFilters } from './types.js';
import type Database from 'better-sqlite3';

export interface DocumentAlias {
  requested_id: string;
  resolved_id: string;
  note: string;
}

const DOCUMENT_ALIASES: Record<string, DocumentAlias> = {
  'EBA/GL/2021/02': {
    requested_id: 'EBA/GL/2021/02',
    resolved_id: 'EBA/GL/2023/03',
    note: 'Resolved to the current consolidated ML/TF Risk Factors Guidelines indexed from the EBA consolidated PDF.',
  },
};

interface RelationshipAliasRow {
  requested_id: string;
  resolved_id: string;
  resolved_title: string;
  relationship_type: string;
}

function tableExists(db: Database.Database, tableName: string): boolean {
  const row = db.prepare(`
    SELECT 1 AS present
    FROM sqlite_master
    WHERE type = 'table' AND name = ?
  `).get(tableName) as { present: number } | undefined;

  return row !== undefined;
}

function getRelationshipAlias(db: Database.Database, ebaId: string): DocumentAlias | null {
  if (!tableExists(db, 'document_relationships')) {
    return null;
  }

  const row = db.prepare(`
    SELECT
      r.target_eba_id AS requested_id,
      r.source_eba_id AS resolved_id,
      d.title AS resolved_title,
      r.relationship_type
    FROM document_relationships r
    JOIN documents d ON d.eba_id = r.source_eba_id
    WHERE r.target_eba_id = ?
      AND r.source_eba_id <> r.target_eba_id
      AND r.relationship_type IN ('consolidates', 'replaces', 'supersedes')
    ORDER BY
      CASE r.relationship_type
        WHEN 'consolidates' THEN 0
        WHEN 'replaces' THEN 1
        WHEN 'supersedes' THEN 2
        ELSE 3
      END,
      COALESCE(d.published_at, '') DESC,
      r.source_eba_id DESC
    LIMIT 1
  `).get(ebaId) as RelationshipAliasRow | undefined;

  if (!row) {
    return null;
  }

  return {
    requested_id: row.requested_id,
    resolved_id: row.resolved_id,
    note: `Resolved through document relationship ${row.relationship_type} to ${row.resolved_id} (${row.resolved_title}).`,
  };
}

export function getDocumentAlias(ebaId: string | undefined, db?: Database.Database): DocumentAlias | null {
  if (!ebaId) {
    return null;
  }

  return DOCUMENT_ALIASES[ebaId] ?? (db ? getRelationshipAlias(db, ebaId) : null);
}

export function resolveDocumentId(ebaId: string, db?: Database.Database): string {
  return getDocumentAlias(ebaId, db)?.resolved_id ?? ebaId;
}

export function resolveSearchFilters(filters: SearchFilters, db?: Database.Database): SearchFilters {
  if (!filters.eba_id) {
    return filters;
  }

  const resolvedId = resolveDocumentId(filters.eba_id, db);
  if (resolvedId === filters.eba_id) {
    return filters;
  }

  return { ...filters, eba_id: resolvedId };
}
