import { diffVersions, getContextForChunks, getCorpusInfo, getDocument, getDocumentStatus, getParagraph, getSection, getToc, getVersions, listDocuments, searchChunks, searchChunksWithMode, validateCitation } from '../db/retrieval.js';
import { buildCitation, buildCitations } from '../citations/formatter.js';
import { buildResponse } from './formatters.js';
import type { Chunk } from '../db/types.js';
import type { CitationObject } from '../citations/formatter.js';
import type { McpResponse } from './formatters.js';
import type {
  EbaCorpusInfoInputType,
  EbaDiffVersionsInputType,
  EbaGetDocumentInputType,
  EbaGetParagraphInputType,
  EbaGetSectionInputType,
  EbaGetStatusInputType,
  EbaGetTocInputType,
  EbaGetVersionsInputType,
  EbaListDocumentsInputType,
  EbaSearchInputType,
  EbaValidateCitationInputType,
} from './schemas.js';

const EBA_ID_PATTERN = /^EBA\/[A-Za-z][A-Za-z-]*\/\d{4}\/\d+$/;

const REGISTERED_TOOLS = [
  'eba_search', 'eba_get_document', 'eba_get_paragraph', 'eba_get_section',
  'eba_get_toc', 'eba_list_documents', 'eba_corpus_info', 'eba_get_status',
  'eba_get_versions', 'eba_validate_citation', 'eba_diff_versions',
] as const;

type SearchResponseMode = EbaSearchInputType['response_mode'];

type SearchCitation = CitationObject | {
  citation_id: string;
  eba_id: string;
  paragraph_ref: string | null;
  page_start: number | null;
  page_end: number | null;
  text: string;
  truncated: boolean;
  truncation_offset: string | null;
  citation: string;
};

type BudgetableCitation = SearchCitation & {
  text: string;
  truncated: boolean;
  truncation_offset: string | null;
};

const SEARCH_RESPONSE_SIZE_BUDGET_CHARS = 50_000;
const SEARCH_SUGGESTED_NEXT_TOOLS = ['eba_get_paragraph', 'eba_get_section', 'eba_get_toc'] as const;

function getDefaultSearchMaxChars(responseMode: SearchResponseMode): number {
  switch (responseMode) {
    case 'compact':
      return 600;
    case 'full':
      return 5_000;
    case 'standard':
      return 1_200;
  }
}

function getDefaultSearchMaxCitations(responseMode: SearchResponseMode): number {
  switch (responseMode) {
    case 'compact':
      return 15;
    case 'full':
      return 5;
    case 'standard':
      return 10;
  }
}

function prioritizeAnchorChunks(anchorChunks: Chunk[], expandedChunks: Chunk[]): Chunk[] {
  const anchorIds = new Set(anchorChunks.map((chunk) => chunk.chunk_id));
  const ordered: Chunk[] = [];
  const seen = new Set<string>();

  for (const chunk of anchorChunks) {
    if (!seen.has(chunk.chunk_id)) {
      ordered.push(chunk);
      seen.add(chunk.chunk_id);
    }
  }

  for (const chunk of expandedChunks) {
    if (!anchorIds.has(chunk.chunk_id) && !seen.has(chunk.chunk_id)) {
      ordered.push(chunk);
      seen.add(chunk.chunk_id);
    }
  }

  return ordered;
}

function compactCitation(citation: CitationObject): SearchCitation {
  return {
    citation_id: citation.citation_id,
    eba_id: citation.eba_id,
    paragraph_ref: citation.paragraph_ref,
    page_start: citation.page_start,
    page_end: citation.page_end,
    text: citation.text,
    truncated: citation.truncated,
    truncation_offset: citation.truncation_offset,
    citation: citation.citation,
  };
}

function countContextChunks(chunks: Chunk[], anchorIds: Set<string>): number {
  return chunks.filter((chunk) => !anchorIds.has(chunk.chunk_id)).length;
}

function measureMcpToolJson(response: McpResponse): number {
  return JSON.stringify(response, null, 2).length;
}

function withReportedResponseSize(response: McpResponse): McpResponse {
  let reportedSize = response.response_size_chars ?? 0;

  for (;;) {
    const nextResponse = {
      ...response,
      response_size_chars: reportedSize,
      response_size_budget_chars: SEARCH_RESPONSE_SIZE_BUDGET_CHARS,
    };
    const measuredSize = measureMcpToolJson(nextResponse);

    if (measuredSize === reportedSize) {
      return nextResponse;
    }

    reportedSize = measuredSize;
  }
}

function getCitationTextTotalLength(citation: BudgetableCitation): number {
  if (!citation.truncation_offset) {
    return citation.text.length;
  }

  const [, totalLengthText] = citation.truncation_offset.split('/').map((part) => part.trim());
  const totalLength = Number.parseInt(totalLengthText ?? '', 10);
  return Number.isFinite(totalLength) ? totalLength : citation.text.length;
}

function shrinkSingleCitation(response: McpResponse): McpResponse {
  const [citation] = response.citations as BudgetableCitation[];
  if (!citation) {
    return response;
  }

  const maxFallbackTextChars = 200;
  const totalLength = getCitationTextTotalLength(citation);
  const nextCitation: BudgetableCitation = {
    ...citation,
    text: citation.text.slice(0, maxFallbackTextChars),
    truncated: citation.text.length > maxFallbackTextChars || citation.truncated,
    truncation_offset: `${Math.min(maxFallbackTextChars, citation.text.length)} / ${totalLength}`,
  };

  return {
    ...response,
    citations: [nextCitation],
  };
}

function withSearchResponseBudget(response: McpResponse, availableCitations: number): McpResponse {
  let limitedResponse = withReportedResponseSize(response);
  let responseSize = measureMcpToolJson(limitedResponse);

  while (responseSize > SEARCH_RESPONSE_SIZE_BUDGET_CHARS && limitedResponse.citations.length > 1) {
    const returnedCitations = limitedResponse.citations.length - 1;
    const omittedCitations = Math.max(availableCitations - returnedCitations, 0);
    const budgetWarning = `eba_search response exceeded ${SEARCH_RESPONSE_SIZE_BUDGET_CHARS} characters; returned ${returnedCitations} of ${availableCitations} citations. Narrow the query or use eba_get_paragraph/eba_get_section for exact context.`;

    limitedResponse = withReportedResponseSize({
      ...limitedResponse,
      citations: limitedResponse.citations.slice(0, returnedCitations),
      response_limited: true,
      limit_reason: 'response_size_chars',
      returned_citations: returnedCitations,
      omitted_citations: omittedCitations,
      response_size_budget_chars: SEARCH_RESPONSE_SIZE_BUDGET_CHARS,
      suggested_next_tools: [...SEARCH_SUGGESTED_NEXT_TOOLS],
      warnings: limitedResponse.warnings.includes(budgetWarning)
        ? limitedResponse.warnings
        : [...limitedResponse.warnings, budgetWarning],
    });
    responseSize = measureMcpToolJson(limitedResponse);
  }

  if (responseSize > SEARCH_RESPONSE_SIZE_BUDGET_CHARS && limitedResponse.citations.length === 1) {
    const budgetWarning = `eba_search response exceeded ${SEARCH_RESPONSE_SIZE_BUDGET_CHARS} characters; preserved one minimal citation shell and truncated its text. Use eba_get_paragraph/eba_get_section for exact context.`;
    limitedResponse = withReportedResponseSize({
      ...shrinkSingleCitation(limitedResponse),
      response_limited: true,
      limit_reason: 'response_size_chars',
      returned_citations: 1,
      omitted_citations: Math.max(availableCitations - 1, 0),
      response_size_budget_chars: SEARCH_RESPONSE_SIZE_BUDGET_CHARS,
      suggested_next_tools: [...SEARCH_SUGGESTED_NEXT_TOOLS],
      warnings: limitedResponse.warnings.includes(budgetWarning)
        ? limitedResponse.warnings
        : [...limitedResponse.warnings, budgetWarning],
    });
  }

  return withReportedResponseSize(limitedResponse);
}

function getSearchAnswerability(citationCount: number, isExactDocumentLookup: boolean): 'exact' | 'partial' | 'no_match' {
  if (citationCount === 0) {
    return 'no_match';
  }

  if (isExactDocumentLookup) {
    return 'exact';
  }

  return citationCount === 1 ? 'exact' : 'partial';
}

export async function handleEbaSearch(input: EbaSearchInputType) {
  try {
    const responseMode = input.response_mode;
    const maxCitations = input.max_citations ?? getDefaultSearchMaxCitations(responseMode);
    const maxChars = input.max_chars ?? getDefaultSearchMaxChars(responseMode);
    const searchResult = await searchChunksWithMode(input.query, input.filters || {}, input.limit || 10, input.search_mode);
    const baseChunks = searchResult.chunks;
    const expandedChunks = input.include_context ? getContextForChunks(baseChunks, 1, 1) : baseChunks;
    const orderedChunks = input.include_context ? prioritizeAnchorChunks(baseChunks, expandedChunks) : expandedChunks;
    const chunks = orderedChunks.slice(0, maxCitations);
    const fullCitations = buildCitations(chunks, '', { maxChars });
    const citations: SearchCitation[] = responseMode === 'compact'
      ? fullCitations.map(compactCitation)
      : fullCitations;

    const anchorIds = new Set(baseChunks.map((chunk) => chunk.chunk_id));
    const availableCitations = orderedChunks.length;
    const omittedCitations = Math.max(availableCitations - citations.length, 0);
    const omittedContext = input.include_context
      ? Math.max(countContextChunks(orderedChunks, anchorIds) - countContextChunks(chunks, anchorIds), 0)
      : 0;
    const citationCapLimited = omittedCitations > 0;
    const warnings = citationCapLimited
      ? [`eba_search returned ${citations.length} of ${availableCitations} available citations after context expansion. Narrow the query, increase max_citations, or use eba_get_paragraph/eba_get_section for exact context.`]
      : [];

    const isExactDocumentLookup = EBA_ID_PATTERN.test(input.query.trim()) || Boolean(input.filters?.eba_id);

    const response = buildResponse(
      getSearchAnswerability(baseChunks.length, isExactDocumentLookup),
      citations,
      {
        documents_considered: [...new Set(chunks.map((chunk) => chunk.eba_id).filter(Boolean))] as string[],
        filters_applied: input.filters || {},
        search_mode: searchResult.search_mode,
        embedding_model: searchResult.embedding_model,
        embeddings_available: searchResult.embeddings_available,
        response_mode: responseMode,
        response_limited: citationCapLimited,
        limit_reason: citationCapLimited ? 'citation_cap' : undefined,
        available_citations: availableCitations,
        returned_citations: citations.length,
        omitted_citations: omittedCitations,
        omitted_context: omittedContext,
        response_size_budget_chars: SEARCH_RESPONSE_SIZE_BUDGET_CHARS,
        suggested_next_tools: citationCapLimited ? [...SEARCH_SUGGESTED_NEXT_TOOLS] : undefined,
        warnings,
      }
    );

    return withSearchResponseBudget(response, availableCitations);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown search error';

    return buildResponse('error', [], { warnings: [message] });
  }
}

export async function handleEbaGetDocument(input: EbaGetDocumentInputType) {
  const document = getDocument(input.eba_id, input.language || 'en');

  if (!document) {
    return buildResponse('no_match', []);
  }

  const chunks = await searchChunks('', { eba_id: input.eba_id }, 50);
  const citations = chunks.slice(0, 5).map(c => buildCitation(c, input.eba_id, { maxChars: input.max_chars }));
  const warnings = [
    'eba_get_document returns metadata plus leading citation chunks only; use eba_get_toc and eba_get_section for section-level retrieval.',
  ];

  if (document.publication_status === 'consultation') {
    warnings.push('Document is in consultation status');
  }

  return {
    ...buildResponse('exact', citations, { warnings }),
    document,
    citation_sample: {
      returned: citations.length,
      max_returned: 5,
      full_document_dump: false,
      navigation_tools: ['eba_get_toc', 'eba_get_section', 'eba_get_paragraph'],
    },
  };
}

export function handleEbaGetVersions(input: EbaGetVersionsInputType) {
  const versions = getVersions(input.eba_id);
  if (!versions) return buildResponse('no_match', []);
  return {
    ...buildResponse('exact', [], {}),
    versions,
  };
}

export function handleEbaDiffVersions(input: EbaDiffVersionsInputType) {
  const result = diffVersions(input.eba_id, input.version_a, input.version_b);

  if (!result) {
    return buildResponse('no_match', []);
  }

  if (result.error) {
    return {
      ...buildResponse('error', [], { warnings: [result.error] }),
      error: result.error,
    };
  }

  return {
    ...buildResponse('exact', [], {}),
    diff: result,
  };
}

export function handleEbaGetParagraph(input: EbaGetParagraphInputType) {
  const paragraphRefs: string[] = input.paragraph_refs?.length
    ? input.paragraph_refs
    : (input.paragraph_ref !== undefined ? [input.paragraph_ref] : []);

  const citations = paragraphRefs.flatMap((paragraphRef) => (
    getParagraph(
      input.eba_id,
      paragraphRef,
      input.language || 'en',
      input.context_before || 0,
      input.context_after || 0,
    ).map((chunk) => ({
      ...buildCitation(chunk, input.eba_id, { maxChars: input.max_chars }),
      is_anchor: chunk.paragraph_ref === paragraphRef,
      is_complete: !chunk.chunk_id.includes(':sub'),
    }))
  ));

  if (citations.length === 0) {
    return buildResponse('no_match', []);
  }

  return buildResponse('exact', citations);
}

export function handleEbaGetSection(input: EbaGetSectionInputType) {
  const chunks = getSection(input.eba_id, input.section, input.language || 'en', input.limit || 200);

  if (chunks.length === 0) {
    return buildResponse('no_match', []);
  }

  return {
    ...buildResponse('exact', chunks.map((chunk) => buildCitation(chunk, input.eba_id, { maxChars: input.max_chars }))),
    section: input.section,
    total_chunks: chunks.length,
  };
}

export function handleEbaGetToc(input: EbaGetTocInputType) {
  const toc = getToc(input.eba_id, input.language || 'en', input.limit || 200);

  if (!toc) {
    return buildResponse('no_match', []);
  }

  return {
    ...buildResponse(toc.length > 0 ? 'exact' : 'no_match', []),
    toc,
    total: toc.length,
  };
}

export function handleEbaListDocuments(input: EbaListDocumentsInputType) {
  const documents = listDocuments(input.filters || {}, input.limit || 20);

  return {
    ...buildResponse(documents.length > 0 ? 'partial' as const : 'no_match' as const, [], {
      filters_applied: input.filters || {},
    }),
    documents,
    total: documents.length,
  };
}

export function handleEbaCorpusInfo(_input: EbaCorpusInfoInputType | undefined) {
  const info = getCorpusInfo();

  return {
    ...buildResponse(info ? 'exact' as const : 'no_match' as const, []),
    corpus_info: info,
    server_capabilities: {
      registered_tools: [...REGISTERED_TOOLS],
      tool_count: REGISTERED_TOOLS.length,
    },
  };
}
export function handleEbaGetStatus(input: EbaGetStatusInputType) {
  const result = getDocumentStatus(input.eba_id);

  if (!result) {
    return buildResponse('no_match', []);
  }

  return {
    ...buildResponse('exact', [], {}),
    status: result,
  };
}

export function handleEbaValidateCitation(input: EbaValidateCitationInputType) {
  const result = validateCitation(input.chunk_id ?? input.citation_id ?? '');
  return {
    ...buildResponse(result.valid ? 'exact' : 'no_match', [], {}),
    validation: result,
  };
}
