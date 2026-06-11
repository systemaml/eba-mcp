import { EMBEDDING_MODEL, SEARCH_MODE } from '../config.js';
import type Database from 'better-sqlite3';
import { hybridSearch } from './hybrid.js';
import { getDb, isVecLoaded } from './sqlite.js';
import { ftsSearch } from './fts.js';
import { addConsultationResponseExclusion, addTopicFilterNoAlias } from './filter-helpers.js';
import { Chunk, Document, CorpusManifest, PersistedTocEntry, SearchFilters, TocConfidence, TocEntry } from './types.js';
import { hasVectorSearch } from './vector.js';
import { getDocumentAlias, resolveDocumentId, resolveSearchFilters } from './aliases.js';

const EBA_ID_PATTERN = /^EBA\/[A-Za-z][A-Za-z-]*\/\d{4}\/\d+$/;
const LARGE_EBA_ID_PATTERN = /^EBA\/LARGE-[A-Za-z]+\/\d{4}\/\d+$/i;
const MIN_DUPLICATE_TEXT_LENGTH = 80;
const MIN_SHARED_TOKEN_COUNT = 12;
const MIN_SMALLER_SIDE_TOKEN_COVERAGE = 0.9;
const MAX_TOC_DEPTH = 3;
const MAX_TOC_TITLE_WORDS = 16;
const NOMIC_EMBED_TEXT_DIM = 768;
const HIGH_TOC_CONFIDENCE = 0.85;
const MEDIUM_TOC_CONFIDENCE = 0.6;

const PREFERRED_DOCUMENT_REGIONS = new Set(['body', 'default']);

const TOC_BOILERPLATE_SECTIONS = new Set([
  '(unsectioned)',
  'background',
  'rationale',
  'next steps',
  'contents',
  'guidelines',
  'reporting requirements',
  'status of these guidelines',
]);

const TOC_PARAGRAPH_STARTERS = [
  'a common ',
  'a number ',
  'after ',
  'another ',
  'before ',
  'by way ',
  'competent authorities ',
  'credit or financial institutions ',
  'firms ',
  'in accordance ',
  'in relation ',
  'many ',
  'other ',
  'several ',
  'some ',
  'the eba ',
  'the factors ',
  'the guidelines ',
  'the international ',
  'the management ',
  'the original ',
  'these guidelines ',
  'there are ',
  'this document ',
  'this section ',
  'through ',
  'to comply ',
  'regarding ',
  'when ',
  'where ',
];

export interface SearchChunksResult {
  chunks: Chunk[];
  search_mode?: 'hybrid' | 'vector' | 'fts_fallback' | 'fts_only';
  embedding_model?: string;
  embeddings_available?: boolean;
  warnings?: string[];
}

type SearchModePreference = 'hybrid' | 'fts';

interface TocCandidate {
  sectionRef: string;
  sectionPath: string;
  level: number;
  confidence: 'high' | 'medium' | 'low';
}

interface TocAccumulator {
  sectionPath: string;
  sectionRef: string;
  level: number;
  parentSectionRef: string | null;
  confidence: 'high' | 'medium' | 'low';
  paragraphRefs: string[];
  seenParagraphRefs: Set<string>;
  firstSequenceNo: number;
  lastSequenceNo: number;
  pageStart: number | null;
  pageEnd: number | null;
  chunkCount: number;
}

interface TocChunkSummary {
  paragraphRefs: string[];
  firstSequenceNo: number | null;
  lastSequenceNo: number | null;
  pageStart: number | null;
  pageEnd: number | null;
  chunkCount: number;
}

interface DocumentIdRow {
  eba_id: string;
}

interface VersionRow {
  version_label: string;
  published_at: string | null;
  is_current: number | boolean | null;
  file_sha256: string;
}

function hasUsableVectorSearch(): boolean {
  const db = getDb();
  return isVecLoaded() && hasVectorSearch(db);
}

function getEffectiveSearchMode(): SearchModePreference {
  return SEARCH_MODE === 'fts_only' ? 'fts' : 'hybrid';
}

function getFtsSearchMode(effectiveMode: SearchModePreference): 'fts_fallback' | 'fts_only' {
  return effectiveMode === 'fts' ? 'fts_only' : 'fts_fallback';
}

function getExpectedEmbeddingDim(model: string): number | null {
  const baseModel = model.trim().toLowerCase().split(':', 1)[0];
  return baseModel === 'nomic-embed-text' ? NOMIC_EMBED_TEXT_DIM : null;
}

function getAliasResolutionWarning(db: Database.Database, query: string, filters: SearchFilters): string | null {
  const queriedAlias = EBA_ID_PATTERN.test(query.trim()) ? getDocumentAlias(query.trim(), db) : null;
  const filteredAlias = getDocumentAlias(filters.eba_id, db);
  const alias = queriedAlias ?? filteredAlias;

  return alias ? `${alias.requested_id} resolved to ${alias.resolved_id}: ${alias.note}` : null;
}

function getEmbeddingCompatibilityWarning(effectiveMode: SearchModePreference): string | null {
  if (effectiveMode === 'fts') {
    return null;
  }

  const manifest = getCorpusInfo();
  if (!manifest?.embedding_model || !manifest.embedding_dim) {
    return 'Vector search disabled: corpus manifest is missing embedding_model or embedding_dim metadata. Rebuild the embedded corpus with the current pipeline; falling back to FTS.';
  }

  if (manifest.embedding_model !== EMBEDDING_MODEL) {
    return `Vector search disabled: runtime EMBEDDING_MODEL=${JSON.stringify(EMBEDDING_MODEL)} does not match corpus embedding_model=${JSON.stringify(manifest.embedding_model)}; falling back to FTS.`;
  }

  const expectedDim = getExpectedEmbeddingDim(EMBEDDING_MODEL);
  if (expectedDim !== null && manifest.embedding_dim !== expectedDim) {
    return `Vector search disabled: runtime EMBEDDING_MODEL=${JSON.stringify(EMBEDDING_MODEL)} expects embedding_dim=${expectedDim}, but corpus manifest has embedding_dim=${manifest.embedding_dim}; falling back to FTS.`;
  }

  return null;
}

function isGeneratedLargeEbaId(ebaId: string | undefined): boolean {
  return Boolean(ebaId && LARGE_EBA_ID_PATTERN.test(ebaId));
}

function getEbaSeriesCode(ebaId: string | undefined): string | null {
  if (!ebaId) {
    return null;
  }

  if (isGeneratedLargeEbaId(ebaId)) {
    return ebaId.slice('EBA/LARGE-'.length).split('/')[0] ?? null;
  }

  return ebaId.slice('EBA/'.length).split('/')[0] ?? null;
}

function normalizeChunkText(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
    .replace(/\s+/g, ' ');
}

function getMeaningfulTokenSet(text: string): Set<string> {
  return new Set(
    normalizeChunkText(text)
      .split(' ')
      .filter((token) => token.length >= 3),
  );
}

function hasStrongTextOverlap(a: string, b: string): boolean {
  const normalizedA = normalizeChunkText(a);
  const normalizedB = normalizeChunkText(b);

  if (
    normalizedA.length < MIN_DUPLICATE_TEXT_LENGTH ||
    normalizedB.length < MIN_DUPLICATE_TEXT_LENGTH
  ) {
    return false;
  }

  if (normalizedA === normalizedB) {
    return true;
  }

  if (normalizedA.includes(normalizedB) || normalizedB.includes(normalizedA)) {
    return true;
  }

  const tokensA = getMeaningfulTokenSet(normalizedA);
  const tokensB = getMeaningfulTokenSet(normalizedB);
  const smaller = tokensA.size <= tokensB.size ? tokensA : tokensB;
  const larger = smaller === tokensA ? tokensB : tokensA;

  if (smaller.size < MIN_SHARED_TOKEN_COUNT) {
    return false;
  }

  let shared = 0;
  for (const token of smaller) {
    if (larger.has(token)) {
      shared += 1;
    }
  }

  return shared >= MIN_SHARED_TOKEN_COUNT && (shared / smaller.size) >= MIN_SMALLER_SIDE_TOKEN_COVERAGE;
}

/**
 * Conservative runtime presentation rule:
 * only demote a generated EBA/LARGE-* hit when the same result set also contains
 * a non-LARGE citation for the same paragraph, same EBA series (e.g. GL), and
 * near-identical/containing text. This keeps standalone LARGE-only hits visible
 * while preferring canonical IDs when both variants cite the same substance.
 */
function findCanonicalDuplicateIndex<T extends Chunk>(chunks: T[], largeChunk: T): number {
  if (!largeChunk.eba_id || !isGeneratedLargeEbaId(largeChunk.eba_id) || !largeChunk.paragraph_ref) {
    return -1;
  }

  const largeSeriesCode = getEbaSeriesCode(largeChunk.eba_id);

  return chunks.findIndex((candidate) => (
    Boolean(candidate.eba_id) &&
    !isGeneratedLargeEbaId(candidate.eba_id) &&
    candidate.paragraph_ref === largeChunk.paragraph_ref &&
    getEbaSeriesCode(candidate.eba_id) === largeSeriesCode &&
    hasStrongTextOverlap(candidate.text, largeChunk.text)
  ));
}

function queryExplicitlyTargetsGeneratedLargeId(query: string, filters: SearchFilters): boolean {
  return isGeneratedLargeEbaId(filters.eba_id) || LARGE_EBA_ID_PATTERN.test(query.trim());
}

export function preferCanonicalEbaResults<T extends Chunk>(
  chunks: T[],
  query: string,
  filters: SearchFilters = {},
  limit = chunks.length,
): T[] {
  if (chunks.length <= 1 || limit <= 0 || queryExplicitlyTargetsGeneratedLargeId(query, filters)) {
    return chunks.slice(0, Math.max(limit, 0));
  }

  const preferred: T[] = [];
  const demoted: T[] = [];

  for (const chunk of chunks) {
    const canonicalDuplicateIndex = findCanonicalDuplicateIndex(chunks, chunk);
    if (canonicalDuplicateIndex >= 0) {
      demoted.push(chunk);
    } else {
      preferred.push(chunk);
    }
  }

  return preferred.concat(demoted).slice(0, limit);
}

function getDocumentRegionPriority(chunk: Chunk): number {
  if (chunk.document_region === undefined || chunk.document_region === null) {
    return 0;
  }

  return PREFERRED_DOCUMENT_REGIONS.has(chunk.document_region.toLowerCase()) ? 0 : 1;
}

function preferBodyDefaultRegionResults<T extends Chunk>(chunks: T[], limit = chunks.length): T[] {
  if (!chunks.some((chunk) => chunk.document_region !== undefined && chunk.document_region !== null)) {
    return chunks.slice(0, Math.max(limit, 0));
  }

  return chunks
    .map((chunk, index) => ({ chunk, index }))
    .sort((a, b) => {
      const regionDelta = getDocumentRegionPriority(a.chunk) - getDocumentRegionPriority(b.chunk);
      return regionDelta !== 0 ? regionDelta : a.index - b.index;
    })
    .slice(0, Math.max(limit, 0))
    .map(({ chunk }) => chunk);
}

function rankChunksForPresentation<T extends Chunk>(
  chunks: T[],
  query: string,
  filters: SearchFilters,
  limit: number,
): T[] {
  const regionPreferred = preferBodyDefaultRegionResults(chunks, chunks.length);
  return preferCanonicalEbaResults(regionPreferred, query, filters, limit);
}

export async function searchChunksWithMode(
  query: string,
  filters: SearchFilters = {},
  limit = 10,
): Promise<SearchChunksResult> {
  const db = getDb();
  const resolvedFilters = resolveSearchFilters(filters, db);
  const aliasWarning = getAliasResolutionWarning(db, query, filters);

  const trimmedQuery = query.trim();
  const exactId = EBA_ID_PATTERN.test(trimmedQuery) ? resolveDocumentId(trimmedQuery, db) : resolvedFilters.eba_id;

  if (exactId && (!trimmedQuery || resolveDocumentId(trimmedQuery, db) === exactId)) {
    const conditions: string[] = ['d.eba_id = ?'];
    const params: unknown[] = [exactId];

    if (resolvedFilters.language) { conditions.push('c.language = ?'); params.push(resolvedFilters.language); }
    if (resolvedFilters.document_type) { conditions.push('d.document_type = ?'); params.push(resolvedFilters.document_type); }
    if (resolvedFilters.publication_status) { conditions.push('d.publication_status = ?'); params.push(resolvedFilters.publication_status); }
    if (resolvedFilters.applicability_status) { conditions.push('d.applicability_status = ?'); params.push(resolvedFilters.applicability_status); }
    addConsultationResponseExclusion(conditions, resolvedFilters);

    const candidateLimit = Math.max(limit * 2, limit + 10);
    params.push(candidateLimit);
    const rows = db.prepare(`
      SELECT c.*, d.eba_id, d.title
      FROM chunks c
      JOIN document_versions dv ON c.document_version_id = dv.version_id
      JOIN documents d ON dv.document_id = d.eba_id
      WHERE ${conditions.join(' AND ')}
      ORDER BY c.sequence_no
      LIMIT ?
    `).all(...params) as Chunk[];
    return {
      chunks: preferBodyDefaultRegionResults(rows, limit),
      embeddings_available: false,
      warnings: aliasWarning ? [aliasWarning] : undefined,
    };
  }

  if (!trimmedQuery) {
    return { chunks: [] };
  }

  const effectiveMode = getEffectiveSearchMode();
  const candidateLimit = Math.max(limit * 2, limit + 10);
  const vectorSearchAvailable = hasUsableVectorSearch();

  if (vectorSearchAvailable) {
    const compatibilityWarning = getEmbeddingCompatibilityWarning(effectiveMode);
    if (compatibilityWarning) {
      return {
        chunks: rankChunksForPresentation(ftsSearch(db, query, resolvedFilters, candidateLimit), trimmedQuery, resolvedFilters, limit),
        search_mode: getFtsSearchMode(effectiveMode),
        embeddings_available: false,
        warnings: aliasWarning ? [aliasWarning, compatibilityWarning] : [compatibilityWarning],
      };
    }
  }

  if (effectiveMode === 'hybrid' && vectorSearchAvailable) {
    const outcome = await hybridSearch(db, trimmedQuery, resolvedFilters, candidateLimit);
    return {
      chunks: rankChunksForPresentation(outcome.results, trimmedQuery, resolvedFilters, limit),
      search_mode: outcome.search_mode,
      embedding_model: outcome.embedding_model,
      embeddings_available: outcome.embeddings_available,
      warnings: aliasWarning ? [aliasWarning] : undefined,
    };
  }

  return {
    chunks: rankChunksForPresentation(ftsSearch(db, query, resolvedFilters, candidateLimit), trimmedQuery, resolvedFilters, limit),
    search_mode: getFtsSearchMode(effectiveMode),
    embeddings_available: false,
    warnings: aliasWarning ? [aliasWarning] : undefined,
  };
}

export async function searchChunks(query: string, filters: SearchFilters = {}, limit = 10): Promise<Chunk[]> {
  const result = await searchChunksWithMode(query, filters, limit);
  return result.chunks;
}

export function getResolvedDocumentId(ebaId: string): string {
  const db = getDb();
  return resolveDocumentId(ebaId, db);
}

export function getDocumentAliasWarning(ebaId: string): string | null {
  const db = getDb();
  const alias = getDocumentAlias(ebaId, db);
  return alias ? `${alias.requested_id} resolved to ${alias.resolved_id}: ${alias.note}` : null;
}

export function getDocument(ebaId: string, language = 'en'): Document | null {
  const db = getDb();
  const resolvedId = resolveDocumentId(ebaId, db);
  return db.prepare(`
    SELECT * FROM documents WHERE eba_id = ? AND language = ?
  `).get(resolvedId, language) as Document | null;
}

export function getVersions(ebaId: string): { version_label: string; published_at: string | null; is_current: boolean; file_sha256: string }[] | null {
  const db = getDb();
  const resolvedId = resolveDocumentId(ebaId, db);

  const doc = db.prepare('SELECT eba_id FROM documents WHERE eba_id = ?').get(resolvedId) as DocumentIdRow | undefined;
  if (!doc) return null;

  const rows = db.prepare(`
      SELECT version_label, published_at, is_current, file_sha256
      FROM document_versions
      WHERE document_id = ?
      ORDER BY published_at DESC
    `).all(resolvedId) as VersionRow[];

  return rows.map(r => ({
    version_label: r.version_label,
    published_at: r.published_at,
    is_current: Boolean(r.is_current),
    file_sha256: r.file_sha256,
  }));
}

export function diffVersions(ebaId: string, versionA: string, versionB: string): {
  eba_id: string;
  version_a: string;
  version_b: string;
  changes: { field: string; old_value: string | null; new_value: string | null }[];
  error?: string;
} | null {
  const db = getDb();
  const resolvedId = resolveDocumentId(ebaId, db);

  const doc = db.prepare('SELECT eba_id FROM documents WHERE eba_id = ?').get(resolvedId) as DocumentIdRow | undefined;
  if (!doc) return null;

  const verA = db.prepare('SELECT version_label, published_at, file_sha256, is_current FROM document_versions WHERE document_id = ? AND version_label = ?').get(resolvedId, versionA) as VersionRow | undefined;
  const verB = db.prepare('SELECT version_label, published_at, file_sha256, is_current FROM document_versions WHERE document_id = ? AND version_label = ?').get(resolvedId, versionB) as VersionRow | undefined;

  if (!verA && !verB) return { eba_id: resolvedId, version_a: versionA, version_b: versionB, changes: [], error: `Versions '${versionA}' and '${versionB}' not found` };
  if (!verA) return { eba_id: resolvedId, version_a: versionA, version_b: versionB, changes: [], error: `Version '${versionA}' not found` };
  if (!verB) return { eba_id: resolvedId, version_a: versionA, version_b: versionB, changes: [], error: `Version '${versionB}' not found` };

  const fields = ['published_at', 'file_sha256', 'is_current'] as const;
  const changes = fields
    .filter(f => verA[f] !== verB[f])
    .map(f => ({ field: f, old_value: String(verA[f] ?? ''), new_value: String(verB[f] ?? '') }));

  return { eba_id: resolvedId, version_a: versionA, version_b: versionB, changes };
}

export function getParagraph(
  ebaId: string,
  paragraphRef: string,
  language = 'en',
  contextBefore = 0,
  contextAfter = 0
): Chunk[] {
  const db = getDb();
  const resolvedId = resolveDocumentId(ebaId, db);
  
  const matches = db.prepare(`
    SELECT c.*, d.eba_id, d.title
    FROM chunks c
    JOIN document_versions dv ON c.document_version_id = dv.version_id
    JOIN documents d ON dv.document_id = d.eba_id
    WHERE d.eba_id = ? AND c.paragraph_ref = ? AND c.language = ?
    ORDER BY c.sequence_no
  `).all(resolvedId, paragraphRef, language) as Chunk[];

  if (matches.length === 0) return [];

  if (contextBefore === 0 && contextAfter === 0) return matches;

  const seen = new Set<string>();
  const withContext: Chunk[] = [];

  for (const match of matches) {
    const rows = db.prepare(`
      SELECT c.*, d.eba_id, d.title
      FROM chunks c
      JOIN document_versions dv ON c.document_version_id = dv.version_id
      JOIN documents d ON dv.document_id = d.eba_id
      WHERE c.document_version_id = ? AND c.sequence_no BETWEEN ? AND ?
      ORDER BY c.sequence_no
    `).all(
      match.document_version_id,
      match.sequence_no - contextBefore,
      match.sequence_no + contextAfter,
    ) as Chunk[];

    for (const row of rows) {
      if (!seen.has(row.chunk_id)) {
        seen.add(row.chunk_id);
        withContext.push(row);
      }
    }
  }

  return withContext;
}

function normalizeSectionRef(section: string): string {
  return section.trim().replace(/\s+/g, ' ').replace(/\.$/, '');
}

export function getSection(
  ebaId: string,
  section: string,
  language = 'en',
  limit = 200,
): Chunk[] {
  const db = getDb();
  const resolvedId = resolveDocumentId(ebaId, db);
  const normalizedSection = normalizeSectionRef(section);
  const tocSectionPrefix = `${normalizedSection}.%`;
  const persistedSectionRows = tableExists(db, 'document_toc') ? db.prepare(`
    SELECT DISTINCT c.*, d.eba_id, d.title
    FROM documents d
    JOIN document_versions dv ON dv.document_id = d.eba_id
    JOIN document_toc dt ON dt.document_version_id = dv.version_id
    JOIN chunks c ON c.document_version_id = dv.version_id
    WHERE d.eba_id = ?
      AND d.language = ?
      AND c.language = ?
      AND (dt.section_ref = ? OR dt.section_ref LIKE ?)
      AND dt.sequence_start IS NOT NULL
      AND dt.sequence_end IS NOT NULL
      AND c.sequence_no BETWEEN dt.sequence_start AND dt.sequence_end
    ORDER BY c.sequence_no
    LIMIT ?
  `).all(resolvedId, language, language, normalizedSection, tocSectionPrefix, limit) as Chunk[] : [];

  if (persistedSectionRows.length > 0) {
    return persistedSectionRows;
  }

  const sectionPrefix = tocSectionPrefix;
  const headingPrefix = `${normalizedSection}. %`;
  const candidateLimit = Math.max(limit * 2, limit + 10);

  return preferBodyDefaultRegionResults(db.prepare(`
    SELECT c.*, d.eba_id, d.title
    FROM chunks c
    JOIN document_versions dv ON c.document_version_id = dv.version_id
    JOIN documents d ON dv.document_id = d.eba_id
    WHERE d.eba_id = ?
      AND c.language = ?
      AND (
        c.paragraph_ref = ?
        OR c.paragraph_ref LIKE ?
        OR c.section_path = ?
        OR c.section_path LIKE ?
        OR c.section_path LIKE ?
      )
    ORDER BY c.sequence_no
    LIMIT ?
  `).all(
    resolvedId,
    language,
    normalizedSection,
    sectionPrefix,
    normalizedSection,
    sectionPrefix,
    headingPrefix,
    candidateLimit,
  ) as Chunk[], limit);
}

export function getToc(ebaId: string, language = 'en', limit = 200): TocEntry[] | null {
  const db = getDb();
  const resolvedId = resolveDocumentId(ebaId, db);
  const doc = db.prepare(`
    SELECT d.eba_id, dv.version_id
    FROM documents d
    JOIN document_versions dv ON dv.document_id = d.eba_id
    WHERE d.eba_id = ? AND d.language = ?
    ORDER BY dv.is_current DESC, dv.published_at DESC, dv.version_id DESC
    LIMIT 1
  `).get(resolvedId, language) as { eba_id: string; version_id: number } | undefined;

  if (!doc) {
    return null;
  }

  const persistedToc = getPersistedToc(db, doc.version_id, language, limit);
  if (persistedToc) {
    return persistedToc;
  }

  const rows = db.prepare(`
    SELECT
      COALESCE(NULLIF(c.section_path, ''), '(unsectioned)') AS section_path,
      c.paragraph_ref,
      c.sequence_no,
      c.page_start,
      c.page_end,
      c.text
    FROM chunks c
    JOIN document_versions dv ON c.document_version_id = dv.version_id
    JOIN documents d ON dv.document_id = d.eba_id
    WHERE d.eba_id = ? AND c.language = ?
    ORDER BY c.sequence_no
  `).all(resolvedId, language) as Array<{
    section_path: string;
    paragraph_ref: string | null;
    sequence_no: number;
    page_start: number | null;
    page_end: number | null;
    text: string;
  }>;

  const tocBySection = new Map<string, TocAccumulator>();
  const activeSectionRefs: string[] = [];
  let hasResetToGuidelinesBody = false;

  for (const row of rows) {
    if (!hasResetToGuidelinesBody && normalizeTocText(row.section_path) === 'Guidelines' && row.sequence_no > 10) {
      tocBySection.clear();
      activeSectionRefs.length = 0;
      hasResetToGuidelinesBody = true;
      continue;
    }

    if (shouldSkipTocRow(row.section_path, row.text)) {
      activeSectionRefs.length = 0;
      continue;
    }

    const candidate = getTocCandidate(row.section_path, row.paragraph_ref, row.text);
    const existingCandidateEntry = candidate ? tocBySection.get(candidate.sectionRef) : undefined;
    if (candidate && existingCandidateEntry && row.sequence_no > existingCandidateEntry.lastSequenceNo + 20) {
      activeSectionRefs.length = 0;
      continue;
    }

    if (candidate) {
      activeSectionRefs[candidate.level - 1] = candidate.sectionRef;
      activeSectionRefs.length = candidate.level;
    }

    const activeSectionRef = activeSectionRefs[activeSectionRefs.length - 1];
    if (!activeSectionRef) {
      continue;
    }

    const activeSection = tocBySection.get(activeSectionRef);
    const entry = activeSection ?? createTocAccumulator(
      activeSectionRef,
      candidate?.sectionRef === activeSectionRef ? candidate : null,
      row.sequence_no,
      row.page_start,
      row.page_end,
    );

    if (candidate?.sectionRef === activeSectionRef) {
      entry.sectionPath = candidate.sectionPath;
      entry.confidence = candidate.confidence;
    }

    addRowToTocAccumulator(entry, row.paragraph_ref, row.sequence_no, row.page_start, row.page_end);
    tocBySection.set(activeSectionRef, entry);
  }

  return [...tocBySection.values()]
    .sort((a, b) => a.firstSequenceNo - b.firstSequenceNo)
    .slice(0, limit)
    .map((entry) => ({
      section_path: entry.sectionPath,
      section_ref: entry.sectionRef,
      level: entry.level,
      parent_section_ref: entry.parentSectionRef,
      confidence: entry.confidence,
      paragraph_refs: entry.paragraphRefs,
      first_paragraph_ref: entry.paragraphRefs[0] ?? null,
      last_paragraph_ref: entry.paragraphRefs[entry.paragraphRefs.length - 1] ?? null,
      page_start: entry.pageStart,
      page_end: entry.pageEnd,
      first_sequence_no: entry.firstSequenceNo,
      last_sequence_no: entry.lastSequenceNo,
      chunk_count: entry.chunkCount,
    }));
}

function tableExists(db: Database.Database, tableName: string): boolean {
  const row = db.prepare(`
    SELECT 1 AS present
    FROM sqlite_master
    WHERE type = 'table' AND name = ?
  `).get(tableName) as { present: number } | undefined;

  return row !== undefined;
}

function columnExists(db: Database.Database, tableName: 'chunks', columnName: string): boolean {
  const rows = db.prepare(`PRAGMA table_info(${tableName})`).all() as Array<{ name: string }>;
  return rows.some((row) => row.name === columnName);
}

function toTocConfidence(confidence: number | null, source: string | null): TocConfidence {
  if (confidence !== null) {
    if (confidence >= HIGH_TOC_CONFIDENCE) {
      return 'high';
    }
    if (confidence >= MEDIUM_TOC_CONFIDENCE) {
      return 'medium';
    }
    return 'low';
  }

  const normalizedSource = source?.toLowerCase() ?? '';
  if (normalizedSource.includes('deterministic') || normalizedSource.includes('parser')) {
    return 'high';
  }
  if (normalizedSource.includes('repair') || normalizedSource.includes('llm')) {
    return 'medium';
  }

  return 'low';
}

function summarizeTocChunks(chunks: Chunk[]): TocChunkSummary {
  const paragraphRefs: string[] = [];
  const seenParagraphRefs = new Set<string>();
  let firstSequenceNo: number | null = null;
  let lastSequenceNo: number | null = null;
  let pageStart: number | null = null;
  let pageEnd: number | null = null;

  for (const chunk of chunks) {
    if (chunk.paragraph_ref && !seenParagraphRefs.has(chunk.paragraph_ref)) {
      paragraphRefs.push(chunk.paragraph_ref);
      seenParagraphRefs.add(chunk.paragraph_ref);
    }

    firstSequenceNo = firstSequenceNo === null ? chunk.sequence_no : Math.min(firstSequenceNo, chunk.sequence_no);
    lastSequenceNo = lastSequenceNo === null ? chunk.sequence_no : Math.max(lastSequenceNo, chunk.sequence_no);
    pageStart = pageStart === null ? chunk.page_start : Math.min(pageStart, chunk.page_start ?? pageStart);
    pageEnd = pageEnd === null ? chunk.page_end : Math.max(pageEnd, chunk.page_end ?? pageEnd);
  }

  return {
    paragraphRefs,
    firstSequenceNo,
    lastSequenceNo,
    pageStart,
    pageEnd,
    chunkCount: chunks.length,
  };
}

function getPersistedTocChunks(
  db: Database.Database,
  row: PersistedTocEntry,
  language: string,
  hasSectionRefColumn: boolean,
): Chunk[] {
  if (hasSectionRefColumn) {
    const sectionRows = db.prepare(`
      SELECT c.*
      FROM chunks c
      WHERE c.document_version_id = ?
        AND c.language = ?
        AND c.section_ref = ?
      ORDER BY c.sequence_no
    `).all(row.document_version_id, language, row.section_ref) as Chunk[];

    if (sectionRows.length > 0) {
      return sectionRows;
    }
  }

  if (row.sequence_start === null || row.sequence_end === null) {
    return [];
  }

  return db.prepare(`
    SELECT c.*
    FROM chunks c
    WHERE c.document_version_id = ?
      AND c.language = ?
      AND c.sequence_no BETWEEN ? AND ?
    ORDER BY c.sequence_no
  `).all(row.document_version_id, language, row.sequence_start, row.sequence_end) as Chunk[];
}

function getPersistedToc(
  db: Database.Database,
  documentVersionId: number,
  language: string,
  limit: number,
): TocEntry[] | null {
  if (!tableExists(db, 'document_toc')) {
    return null;
  }

  const rows = db.prepare(`
    SELECT
      document_version_id,
      section_ref,
      title,
      level,
      parent_section_ref,
      page_start,
      page_end,
      sequence_start,
      sequence_end,
      confidence,
      source
    FROM document_toc
    WHERE document_version_id = ?
    ORDER BY COALESCE(sequence_start, 9223372036854775807), level, section_ref
    LIMIT ?
  `).all(documentVersionId, limit) as PersistedTocEntry[];

  if (rows.length === 0) {
    return null;
  }

  const hasSectionRefColumn = columnExists(db, 'chunks', 'section_ref');

  return rows.map((row) => {
    const summary = summarizeTocChunks(getPersistedTocChunks(db, row, language, hasSectionRefColumn));

    return {
      section_path: row.title,
      section_ref: row.section_ref,
      level: row.level,
      parent_section_ref: row.parent_section_ref,
      confidence: toTocConfidence(row.confidence, row.source),
      paragraph_refs: summary.paragraphRefs,
      first_paragraph_ref: summary.paragraphRefs[0] ?? null,
      last_paragraph_ref: summary.paragraphRefs[summary.paragraphRefs.length - 1] ?? null,
      page_start: row.page_start ?? summary.pageStart,
      page_end: row.page_end ?? summary.pageEnd,
      first_sequence_no: row.sequence_start ?? summary.firstSequenceNo ?? 0,
      last_sequence_no: row.sequence_end ?? summary.lastSequenceNo ?? row.sequence_start ?? 0,
      chunk_count: summary.chunkCount,
    };
  });
}

function normalizeTocText(text: string): string {
  return text.trim().replace(/\s+/g, ' ').replace(/[.…:;]+$/u, '').trim();
}

function getFirstLine(text: string): string {
  return normalizeTocText(text.split(/\r?\n/, 1)[0] ?? '');
}

function extractNumericPrefix(text: string): string | null {
  const match = normalizeTocText(text).match(/^(\d+(?:\.\d+){0,4})\.?\s+/);
  return match?.[1] ?? null;
}

function isConsultationTocPath(sectionPath: string): boolean {
  const normalized = sectionPath.toLowerCase();
  return normalized.includes('do you have any comments') ||
    normalized.includes('feedback on') ||
    normalized.includes('summary of responses') ||
    normalized.includes('public consultation') ||
    normalized.includes('analysis of responses') ||
    normalized.includes('consultation responses');
}

function isBackMatterTocPath(sectionPath: string): boolean {
  const normalized = sectionPath.toLowerCase();
  return normalized.includes('accompanying documents') || normalized.includes('feedback table');
}

function shouldSkipTocRow(sectionPath: string, text: string): boolean {
  const normalizedSectionPath = normalizeTocText(sectionPath);
  const firstLine = getFirstLine(text);
  return isConsultationTocPath(normalizedSectionPath) ||
    isConsultationTocPath(firstLine) ||
    isBackMatterTocPath(normalizedSectionPath) ||
    isBackMatterTocPath(firstLine);
}

function isBoilerplateTocPath(sectionPath: string): boolean {
  const normalized = normalizeTocText(sectionPath).toLowerCase();
  return TOC_BOILERPLATE_SECTIONS.has(normalized) || isConsultationTocPath(normalized);
}

function stripNumericPrefix(title: string, prefix: string): string {
  const escapedPrefix = prefix.replace(/\./g, '\\.');
  return normalizeTocText(title.replace(new RegExp(`^${escapedPrefix}(?:\.)?\\s*`), ''));
}

function isLikelyParagraphTitle(title: string, prefix: string): boolean {
  const remainder = stripNumericPrefix(title, prefix).toLowerCase();
  const wordCount = remainder.split(/\s+/).filter(Boolean).length;
  if (wordCount > MAX_TOC_TITLE_WORDS) {
    return true;
  }

  return TOC_PARAGRAPH_STARTERS.some((starter) => remainder.startsWith(starter));
}

function getTocCandidate(sectionPath: string, paragraphRef: string | null, text: string): TocCandidate | null {
  const normalizedSectionPath = normalizeTocText(sectionPath);
  const firstLine = getFirstLine(text);

  if (isBoilerplateTocPath(normalizedSectionPath) && !paragraphRef?.includes('.')) {
    return null;
  }

  const sectionPrefix = extractNumericPrefix(normalizedSectionPath);
  if (sectionPrefix && sectionPrefix.split('.').length <= MAX_TOC_DEPTH && !isLikelyParagraphTitle(normalizedSectionPath, sectionPrefix)) {
    return {
      sectionRef: sectionPrefix,
      sectionPath: normalizedSectionPath,
      level: sectionPrefix.split('.').length,
      confidence: 'high',
    };
  }

  const paragraphPrefix = paragraphRef && /^\d+(?:\.\d+){1,3}$/.test(paragraphRef) ? paragraphRef : null;
  const firstLinePrefix = paragraphPrefix ? extractNumericPrefix(firstLine) : null;
  if (
    paragraphPrefix &&
    firstLinePrefix === paragraphPrefix &&
    paragraphPrefix.split('.').length <= MAX_TOC_DEPTH &&
    !isLikelyParagraphTitle(firstLine, paragraphPrefix)
  ) {
    return {
      sectionRef: paragraphPrefix,
      sectionPath: firstLine,
      level: paragraphPrefix.split('.').length,
      confidence: 'medium',
    };
  }

  return null;
}

function getParentSectionRef(sectionRef: string): string | null {
  const parts = sectionRef.split('.');
  return parts.length > 1 ? parts.slice(0, -1).join('.') : null;
}

function createTocAccumulator(
  sectionRef: string,
  candidate: TocCandidate | null,
  sequenceNo: number,
  pageStart: number | null,
  pageEnd: number | null,
): TocAccumulator {
  return {
    sectionPath: candidate?.sectionPath ?? sectionRef,
    sectionRef,
    level: candidate?.level ?? sectionRef.split('.').length,
    parentSectionRef: getParentSectionRef(sectionRef),
    confidence: candidate?.confidence ?? 'low',
    paragraphRefs: [],
    seenParagraphRefs: new Set<string>(),
    firstSequenceNo: sequenceNo,
    lastSequenceNo: sequenceNo,
    pageStart,
    pageEnd,
    chunkCount: 0,
  };
}

function addRowToTocAccumulator(
  entry: TocAccumulator,
  paragraphRef: string | null,
  sequenceNo: number,
  pageStart: number | null,
  pageEnd: number | null,
): void {
  if (paragraphRef && !entry.seenParagraphRefs.has(paragraphRef)) {
    entry.paragraphRefs.push(paragraphRef);
    entry.seenParagraphRefs.add(paragraphRef);
  }

  entry.lastSequenceNo = sequenceNo;
  entry.pageStart = entry.pageStart === null ? pageStart : Math.min(entry.pageStart, pageStart ?? entry.pageStart);
  entry.pageEnd = entry.pageEnd === null ? pageEnd : Math.max(entry.pageEnd, pageEnd ?? entry.pageEnd);
  entry.chunkCount += 1;
}

export function getContextForChunks(chunks: Chunk[], contextBefore = 1, contextAfter = 1): Chunk[] {
  if (chunks.length === 0 || (contextBefore === 0 && contextAfter === 0)) return chunks;
  const db = getDb();
  const seen = new Set<string>();
  const results: Chunk[] = [];

  for (const chunk of chunks) {
    const rows = db.prepare(`
      SELECT c.*, d.eba_id, d.title
      FROM chunks c
      JOIN document_versions dv ON c.document_version_id = dv.version_id
      JOIN documents d ON dv.document_id = d.eba_id
      WHERE c.document_version_id = ? AND c.sequence_no BETWEEN ? AND ?
      ORDER BY c.sequence_no
    `).all(
      chunk.document_version_id,
      chunk.sequence_no - contextBefore,
      chunk.sequence_no + contextAfter,
    ) as Chunk[];

    for (const row of rows) {
      if (!seen.has(row.chunk_id)) {
        seen.add(row.chunk_id);
        results.push(row);
      }
    }
  }

  return results;
}

export function listDocuments(filters: SearchFilters = {}, limit = 20): Document[] {
  const db = getDb();
  const conditions: string[] = [];
  const params: unknown[] = [];
  if (filters.document_type) { conditions.push('document_type = ?'); params.push(filters.document_type); }
  addTopicFilterNoAlias(conditions, params, filters);
  if (filters.publication_status) { conditions.push('publication_status = ?'); params.push(filters.publication_status); }
  if (filters.applicability_status) { conditions.push('applicability_status = ?'); params.push(filters.applicability_status); }
  if (filters.language) { conditions.push('language = ?'); params.push(filters.language); }
  const where = conditions.length ? `WHERE ${conditions.join(' AND ')}` : '';
  params.push(limit);
  return db.prepare(`SELECT * FROM documents ${where} LIMIT ?`).all(...params) as Document[];
}

export interface DocumentStatus {
  eba_id: string;
  publication_status: string;
  applicability_status: string;
  published_at: string | null;
  application_date: string | null;
  language: string;
  is_consultation: boolean;
  is_superseded: boolean;
  is_partially_superseded: boolean;
  superseded_by: string[];
  amended_by: string[];
  warnings: string[];
}

function getDocumentStatusForId(ebaId: string, resolveAlias: boolean): DocumentStatus | null {
  const db = getDb();
  const alias = resolveAlias ? getDocumentAlias(ebaId, db) : null;
  const resolvedId = alias?.resolved_id ?? ebaId;

  const doc = db.prepare(`
    SELECT eba_id, publication_status, applicability_status, published_at, application_date, language
    FROM documents WHERE eba_id = ?
  `).get(resolvedId) as { eba_id: string; publication_status: string; applicability_status: string; published_at: string | null; application_date: string | null; language: string } | undefined;

  if (!doc) return null;

  const amendedByRows = db.prepare(`
    SELECT source_eba_id FROM document_relationships
    WHERE target_eba_id = ? AND relationship_type = 'amends'
  `).all(resolvedId) as { source_eba_id: string }[];
  const amended_by = amendedByRows.map(r => r.source_eba_id);

  const supersededTargetRows = db.prepare(`
    SELECT source_eba_id FROM document_relationships
    WHERE target_eba_id = ? AND relationship_type IN ('supersedes', 'replaces')
  `).all(resolvedId) as { source_eba_id: string }[];
  const superseded_by = supersededTargetRows.map(r => r.source_eba_id);

  const is_consultation = doc.publication_status.includes('consultation');
  const is_superseded = superseded_by.length > 0;
  const is_partially_superseded = amended_by.length > 0 && !is_superseded;

  const warnings: string[] = [];
  if (alias) warnings.push(`${alias.requested_id} resolved to ${alias.resolved_id}: ${alias.note}`);
  if (is_consultation) warnings.push('Document is in consultation status');
  if (is_superseded) warnings.push(`Document superseded by ${superseded_by[0]}`);

  return {
    eba_id: doc.eba_id,
    publication_status: doc.publication_status,
    applicability_status: doc.applicability_status,
    published_at: doc.published_at,
    application_date: doc.application_date,
    language: doc.language,
    is_consultation,
    is_superseded,
    is_partially_superseded,
    superseded_by,
    amended_by,
    warnings,
  };
}

export function getDocumentStatus(ebaId: string): DocumentStatus | null {
  return getDocumentStatusForId(ebaId, true);
}

export interface CitationValidation {
  valid: boolean;
  chunk_exists: boolean;
  document_eba_id: string | null;
  publication_status: string | null;
  applicability_status: string | null;
  is_superseded: boolean;
  warnings: string[];
}

export function validateCitation(chunkId: string): CitationValidation {
  const db = getDb();

  const chunk = db.prepare(`
    SELECT c.chunk_id, dv.document_id
    FROM chunks c
    JOIN document_versions dv ON c.document_version_id = dv.version_id
    WHERE c.chunk_id = ?
  `).get(chunkId) as { chunk_id: string; document_id: string } | undefined;

  if (!chunk) {
    return { valid: false, chunk_exists: false, document_eba_id: null, publication_status: null, applicability_status: null, is_superseded: false, warnings: [] };
  }

  const status = getDocumentStatusForId(chunk.document_id, false);
  if (!status) {
    return { valid: false, chunk_exists: true, document_eba_id: chunk.document_id, publication_status: null, applicability_status: null, is_superseded: false, warnings: [] };
  }

  return {
    valid: true,
    chunk_exists: true,
    document_eba_id: chunk.document_id,
    publication_status: status.publication_status,
    applicability_status: status.applicability_status,
    is_superseded: status.is_superseded,
    warnings: status.warnings,
  };
}

export function getCorpusInfo(): CorpusManifest | null {
  const db = getDb();
  return db.prepare('SELECT * FROM corpus_manifest LIMIT 1').get() as CorpusManifest | null;
}
