import { diffVersions, getContextForChunks, getCorpusInfo, getDocument, getDocumentStatus, getParagraph, getSection, getToc, getVersions, listDocuments, searchChunks, searchChunksWithMode, validateCitation } from '../db/retrieval.js';
import { buildCitation, buildCitations } from '../citations/formatter.js';
import { buildResponse } from './formatters.js';
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
    const searchResult = await searchChunksWithMode(input.query, input.filters || {}, input.limit || 10);
    const baseChunks = searchResult.chunks;
    const chunks = input.include_context ? getContextForChunks(baseChunks, 1, 1) : baseChunks;
    const citations = buildCitations(chunks, '');

    const isExactDocumentLookup = EBA_ID_PATTERN.test(input.query.trim()) || Boolean(input.filters?.eba_id);

    return buildResponse(
      getSearchAnswerability(baseChunks.length, isExactDocumentLookup),
      citations,
      {
        documents_considered: [...new Set(chunks.map((chunk) => chunk.eba_id).filter(Boolean))] as string[],
        filters_applied: input.filters || {},
        search_mode: searchResult.search_mode,
      }
    );
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
  const citations = chunks.slice(0, 5).map(c => buildCitation(c, input.eba_id));
  const warnings = document.publication_status === 'consultation' ? ['Document is in consultation status'] : [];

  return {
    ...buildResponse('exact', citations, { warnings }),
    document,
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
  const chunks = getParagraph(
    input.eba_id,
    input.paragraph_ref,
    input.language || 'en',
    input.context_before || 0,
    input.context_after || 0,
  );

  if (chunks.length === 0) {
    return buildResponse('no_match', []);
  }

  const citations = chunks.map(c => buildCitation(c, input.eba_id));
  return buildResponse('exact', citations);
}

export function handleEbaGetSection(input: EbaGetSectionInputType) {
  const chunks = getSection(input.eba_id, input.section, input.language || 'en', input.limit || 200);

  if (chunks.length === 0) {
    return buildResponse('no_match', []);
  }

  return {
    ...buildResponse('exact', chunks.map((chunk) => buildCitation(chunk, input.eba_id))),
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
  const result = validateCitation(input.chunk_id);
  return {
    ...buildResponse(result.valid ? 'exact' : 'no_match', [], {}),
    validation: result,
  };
}
