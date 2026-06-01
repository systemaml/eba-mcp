import Database from 'better-sqlite3';

import { Chunk } from './types.js';

export interface VectorResult extends Chunk {
  eba_id: string;
  title: string;
  distance: number;
}

export function hasVectorSearch(db: Database.Database): boolean {
  const row = db
    .prepare(
      `SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'`,
    )
    .get() as { name: string } | undefined;
  return row !== undefined;
}

export function vectorSearch(
  db: Database.Database,
  queryEmbedding: Float32Array,
  limit: number = 10,
): VectorResult[] {
  return db
    .prepare(
      `
      SELECT c.*, d.eba_id, d.title, v.distance
      FROM chunks_vec v
      JOIN chunks c ON v.rowid = c.rowid
      JOIN document_versions dv ON c.document_version_id = dv.version_id
      JOIN documents d ON dv.document_id = d.eba_id
      WHERE v.embedding MATCH ? AND k = ?
      ORDER BY v.distance
    `,
    )
    .all(queryEmbedding, limit) as VectorResult[];
}
