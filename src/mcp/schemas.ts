import { z } from 'zod';

// ── Reusable string primitives ─────────────────────────────────────────────

/** Trim + collapse internal whitespace runs to a single space. */
const normalizeWhitespace = (s: string): string => s.trim().replace(/\s+/g, ' ');

/**
 * Free-text search query.
 * Max 500 chars; leading/trailing and internal whitespace is normalised.
 */
export const QueryString = z
  .string()
  .transform(normalizeWhitespace)
  .pipe(z.string().min(1, 'Query must not be empty').max(500, 'Query exceeds 500 characters'));

/**
 * EBA document ID.
 * Accepts canonical IDs (EBA/GL/2021/02) and generated LARGE-* IDs (EBA/LARGE-RTS/2022/1).
 * Max 40 chars.
 */
export const EbaId = z
  .string()
  .max(40, 'EBA ID exceeds 40 characters')
  .regex(
    /^EBA\/[A-Za-z][A-Za-z-]*\/\d{4}\/\d+$/,
    'Invalid EBA document ID (expected EBA/<TYPE>/YYYY/N where TYPE may include hyphens, e.g. LARGE-GL)',
  );

/**
 * Paragraph reference — e.g. "4.1.2", "p.23", "Title I".
 * Max 50 chars; allows letters, digits, spaces, and common structural punctuation.
 */
export const ParagraphRef = z
  .string()
  .min(1, 'paragraph_ref must not be empty')
  .max(50, 'paragraph_ref exceeds 50 characters')
  .regex(
    /^[A-Za-z0-9][A-Za-z0-9 ._/-]*$/,
    'paragraph_ref contains invalid characters (allowed: letters, digits, space, . _ / -)',
  );

/**
 * Chunk ID — e.g. "EBA-GL-2021-02:001921c3:en:p:seq-527".
 * Max 240 chars; allows letters, digits, colons, hyphens, underscores.
 */
export const ChunkId = z
  .string()
  .min(1, 'chunk_id must not be empty')
  .max(240, 'chunk_id exceeds 240 characters')
  .regex(
    /^[A-Za-z0-9][A-Za-z0-9:_-]*$/,
    'chunk_id contains invalid characters (allowed: letters, digits, : _ -)',
  );

/**
 * Version label — e.g. "1.0", "2022-01", "v2.1".
 * Max 100 chars; allows letters, digits, and common version punctuation.
 */
export const VersionLabel = z
  .string()
  .min(1, 'Version label must not be empty')
  .max(100, 'Version label exceeds 100 characters')
  .regex(
    /^[A-Za-z0-9][A-Za-z0-9 ._/-]*$/,
    'Version label contains invalid characters (allowed: letters, digits, space, . _ / -)',
  );

/**
 * Bounded filter string for metadata fields (document_type, topic, etc.).
 * Max 80 chars; blocks null bytes and ASCII control characters.
 * Permits printable text including slash, comma, parentheses for values like "AML/CFT".
 */
export const FilterString = z
  .string()
  .min(1)
  .max(80, 'Filter value exceeds 80 characters')
  .regex(/^[^\x00-\x1f\x7f]+$/, 'Filter value contains invalid control characters');

const Language = z.literal('en');

// ── Shared filter objects ──────────────────────────────────────────────────

const SearchFilters = z
  .object({
    document_type: FilterString.optional(),
    topic: FilterString.optional(),
    publication_status: FilterString.optional(),
    applicability_status: FilterString.optional(),
    language: Language.optional(),
    eba_id: EbaId.optional(),
  })
  .strict();

// ── Per-tool input schemas ─────────────────────────────────────────────────

export const EbaSearchInput = z
  .object({
    query: QueryString,
    filters: SearchFilters.optional(),
    limit: z.number().int().min(1).max(50).default(10),
    include_context: z.boolean().default(false),
  })
  .strict();

export const EbaGetDocumentInput = z
  .object({
    eba_id: EbaId,
    language: Language.default('en'),
  })
  .strict();

export const EbaGetParagraphInput = z
  .object({
    eba_id: EbaId,
    paragraph_ref: ParagraphRef,
    language: Language.default('en'),
    context_before: z.number().int().min(0).max(3).default(0),
    context_after: z.number().int().min(0).max(3).default(0),
  })
  .strict();

export const EbaListDocumentsInput = z
  .object({
    filters: z
      .object({
        document_type: FilterString.optional(),
        topic: FilterString.optional(),
        publication_status: FilterString.optional(),
        applicability_status: FilterString.optional(),
        language: Language.optional(),
      })
      .strict()
      .optional(),
    limit: z.number().int().min(1).max(100).default(20),
  })
  .strict();

export const EbaCorpusInfoInput = z.object({}).strict().optional();

export const EbaGetStatusInput = z
  .object({
    eba_id: EbaId,
  })
  .strict();

export const EbaGetVersionsInput = z
  .object({
    eba_id: EbaId,
  })
  .strict();

export const EbaValidateCitationInput = z
  .object({
    chunk_id: ChunkId,
  })
  .strict();

export const EbaDiffVersionsInput = z
  .object({
    eba_id: EbaId,
    version_a: VersionLabel,
    version_b: VersionLabel,
  })
  .strict();

// ── Inferred types ─────────────────────────────────────────────────────────

export type EbaSearchInputType = z.infer<typeof EbaSearchInput>;
export type EbaGetDocumentInputType = z.infer<typeof EbaGetDocumentInput>;
export type EbaGetParagraphInputType = z.infer<typeof EbaGetParagraphInput>;
export type EbaListDocumentsInputType = z.infer<typeof EbaListDocumentsInput>;
export type EbaCorpusInfoInputType = z.infer<NonNullable<typeof EbaCorpusInfoInput>>;
export type EbaGetStatusInputType = z.infer<typeof EbaGetStatusInput>;
export type EbaGetVersionsInputType = z.infer<typeof EbaGetVersionsInput>;
export type EbaValidateCitationInputType = z.infer<typeof EbaValidateCitationInput>;
export type EbaDiffVersionsInputType = z.infer<typeof EbaDiffVersionsInput>;
