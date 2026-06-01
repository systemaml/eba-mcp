import Database from 'better-sqlite3';
import path from 'path';
import * as sqliteVec from 'sqlite-vec';

let db: Database.Database | null = null;
let vecLoaded = false;

function hasChunksVecTable(database: Database.Database): boolean {
  const row = database
    .prepare("SELECT name FROM sqlite_master WHERE type='table' AND name='chunks_vec'")
    .get() as { name: string } | undefined;
  return row !== undefined;
}

function loadVecExtension(database: Database.Database): void {
  if (!hasChunksVecTable(database)) {
    vecLoaded = false;
    return;
  }

  try {
    sqliteVec.load(database);
    vecLoaded = true;
  } catch (err) {
    if (process.env.EBA_DEBUG) {
      process.stderr.write(`sqlite-vec not loaded (vector search unavailable): ${(err as Error).message}\n`);
    }
    vecLoaded = false;
  }
}

export function isVecLoaded(): boolean {
  return vecLoaded;
}

export function getDb(dbPath?: string): Database.Database {
  if (db) return db;
  const p = dbPath || process.env.EBA_DB_PATH || './data/eba.db';
  db = new Database(path.resolve(p), { readonly: true });
  loadVecExtension(db);
  return db;
}

export function initDb(dbPath: string): Database.Database {
  if (db) { db.close(); db = null; }
  db = new Database(path.resolve(dbPath), { readonly: true });
  loadVecExtension(db);
  return db;
}
