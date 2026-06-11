import './env.js';
import { resolveEnvPath } from './env.js';

export const DB_PATH = resolveEnvPath('EBA_DB_PATH', 'data/corpora/eba-corpus.db');

export type SearchMode = 'auto' | 'fts_only' | 'hybrid';

const SEARCH_MODES = new Set<SearchMode>(['auto', 'fts_only', 'hybrid']);

const STRICT_INT_RE = /^\d+$/;
const STRICT_FLOAT_RE = /^\d+(?:\.\d+)?$/;

function parseIntEnv(value: string | undefined, fallback: number): number {
  if (!value || !STRICT_INT_RE.test(value)) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function parseFloatEnv(value: string | undefined, fallback: number): number {
  if (!value || !STRICT_FLOAT_RE.test(value)) return fallback;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function parseSearchMode(value: string | undefined, fallback: SearchMode): SearchMode {
  return SEARCH_MODES.has(value as SearchMode) ? value as SearchMode : fallback;
}

export const OLLAMA_URL = process.env.OLLAMA_URL || 'http://localhost:11434';
export const OLLAMA_TIMEOUT_MS = parseIntEnv(process.env.OLLAMA_TIMEOUT_MS, 5000);
export const EMBEDDING_MODEL = process.env.EMBEDDING_MODEL || 'nomic-embed-text';
export const RRF_K = parseIntEnv(process.env.RRF_K, 60);
export const RRF_WEIGHT_FTS = parseFloatEnv(process.env.RRF_WEIGHT_FTS, 1.0);
export const RRF_WEIGHT_VEC = parseFloatEnv(process.env.RRF_WEIGHT_VEC, 1.0);
export const SEARCH_MODE = parseSearchMode(process.env.EBA_SEARCH_MODE, 'auto');
