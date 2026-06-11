import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

const VALID_ENV_KEY = /^[A-Za-z_][A-Za-z0-9_]*$/;
const DEFAULT_DB_PATH = 'data/corpora/eba-corpus.db';

export interface RuntimeEnvContext {
  repoRoot: string;
  envPath: string | null;
  loadedKeys: Set<string>;
}

let loaded = false;
let context: RuntimeEnvContext | null = null;

function parseEnvLine(line: string, lineNumber: number, filePath: string): [string, string] | undefined {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('#')) {
    return undefined;
  }

  const withoutExport = trimmed.startsWith('export ') ? trimmed.slice('export '.length).trimStart() : trimmed;
  const equalsIndex = withoutExport.indexOf('=');
  if (equalsIndex < 0) {
    throw new Error(`Invalid .env entry at ${filePath}:${lineNumber}: missing '='`);
  }

  const key = withoutExport.slice(0, equalsIndex).trim();
  if (!VALID_ENV_KEY.test(key)) {
    throw new Error(`Invalid .env key at ${filePath}:${lineNumber}: ${key}`);
  }

  let value = withoutExport.slice(equalsIndex + 1).trim();
  if (value.length >= 2 && ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'")))) {
    value = value.slice(1, -1);
  } else {
    const commentIndex = value.search(/\s#/);
    if (commentIndex >= 0) {
      value = value.slice(0, commentIndex).trimEnd();
    }
  }

  return [key, value];
}

export function parseEnv(content: string, filePath = '.env'): Record<string, string> {
  const values: Record<string, string> = {};
  const lines = content.split(/\r?\n/);
  lines.forEach((line, index) => {
    const parsed = parseEnvLine(line, index + 1, filePath);
    if (!parsed) {
      return;
    }
    const [key, value] = parsed;
    values[key] = value;
  });
  return values;
}

export function findRepoRoot(startDir = process.cwd()): string {
  let current = path.resolve(startDir);
  while (true) {
    if (existsSync(path.join(current, 'package.json'))) {
      return current;
    }
    const parent = path.dirname(current);
    if (parent === current) {
      return path.resolve(startDir);
    }
    current = parent;
  }
}

export function loadEnv(): RuntimeEnvContext {
  if (loaded && context) {
    return context;
  }

  const repoRoot = findRepoRoot();
  const envPath = path.join(repoRoot, '.env');
  const loadedKeys = new Set<string>();

  if (existsSync(envPath)) {
    const values = parseEnv(readFileSync(envPath, 'utf8'), envPath);
    for (const [key, value] of Object.entries(values)) {
      if (process.env[key] === undefined) {
        process.env[key] = value;
        loadedKeys.add(key);
      }
    }
  }

  context = {
    repoRoot,
    envPath: existsSync(envPath) ? envPath : null,
    loadedKeys,
  };
  loaded = true;
  return context;
}

export function resolveEnvPath(key: string, fallback: string): string {
  const envContext = loadEnv();
  const configured = process.env[key];
  if (!configured) {
    return path.resolve(envContext.repoRoot, fallback);
  }
  if (!path.isAbsolute(configured) && envContext.envPath && envContext.loadedKeys.has(key)) {
    return path.resolve(path.dirname(envContext.envPath), configured);
  }
  return configured;
}

export function resolveDbPath(args: string[] = process.argv.slice(2)): string {
  const dbIndex = args.indexOf('--db');
  if (dbIndex >= 0) {
    return args[dbIndex + 1] ?? '';
  }
  return resolveEnvPath('EBA_DB_PATH', DEFAULT_DB_PATH);
}

loadEnv();
