export const DB_PATH = process.env.EBA_DB_PATH || './data/eba.db';

export type SearchMode = 'auto' | 'fts_only' | 'hybrid';

function parseIntEnv(value: string | undefined, fallback: number): number {
  const parsed = Number.parseInt(value ?? '', 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export const OLLAMA_URL = process.env.OLLAMA_URL || 'http://localhost:11434';
export const OLLAMA_TIMEOUT_MS = parseIntEnv(process.env.OLLAMA_TIMEOUT_MS, 5000);
export const EMBEDDING_MODEL = process.env.EMBEDDING_MODEL || 'nomic-embed-text';
export const RRF_K = parseInt(process.env.RRF_K || '60', 10);
export const RRF_WEIGHT_FTS = parseFloat(process.env.RRF_WEIGHT_FTS || '1.0');
export const RRF_WEIGHT_VEC = parseFloat(process.env.RRF_WEIGHT_VEC || '1.0');
export const SEARCH_MODE: SearchMode = (process.env.EBA_SEARCH_MODE as SearchMode) || 'auto';
