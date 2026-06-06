import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

import { initDb } from '../db/sqlite.js';
import {
  EbaCorpusInfoInput,
  EbaDiffVersionsInput,
  EbaGetDocumentInput,
  EbaGetParagraphInput,
  EbaGetSectionInput,
  EbaGetStatusInput,
  EbaGetTocInput,
  EbaGetVersionsInput,
  EbaListDocumentsInput,
  EbaSearchInput,
  EbaValidateCitationInput,
} from './schemas.js';
import {
  handleEbaCorpusInfo,
  handleEbaDiffVersions,
  handleEbaGetDocument,
  handleEbaGetParagraph,
  handleEbaGetSection,
  handleEbaGetStatus,
  handleEbaGetToc,
  handleEbaGetVersions,
  handleEbaListDocuments,
  handleEbaSearch,
  handleEbaValidateCitation,
} from './tools.js';

const EBA_ID_PATTERN = '^EBA/[A-Za-z][A-Za-z-]*/\\d{4}/\\d+$';
const CHUNK_ID_PATTERN = '^[A-Za-z0-9][A-Za-z0-9:_-]*$';
const PARAGRAPH_REF_PATTERN = '^[A-Za-z0-9][A-Za-z0-9 ._/-]*$';
const SECTION_REF_PATTERN = '^[A-Za-z0-9][A-Za-z0-9 ._/-]*$';
const VERSION_LABEL_PATTERN = '^[A-Za-z0-9][A-Za-z0-9 ._/-]*$';
const FILTER_STRING_PATTERN = '^[^\\x00-\\x1f\\x7f]+$';

const FILTER_PROPERTIES = {
  document_type: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
  topic: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
  publication_status: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
  applicability_status: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
  language: { type: 'string', enum: ['en'] },
  eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN },
};

const TOOLS = [
  {
    name: 'eba_search',
    description:
      'Discover citation-ready excerpts from the English EBA corpus. Start here for unknown paragraphs or concepts, then use eba_get_paragraph, eba_get_section, or eba_get_toc for navigation. Use English regulatory terms and focused searches. Supports filters.eba_id, document_type, topic, publication_status, applicability_status, and language=en. Warning: paragraph_ref can be null for headings/tables/unnumbered chunks; use citation_id via eba_validate_citation or section/page context when paragraph navigation is unavailable. Returns excerpts and citations, not legal advice.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        query: {
          type: 'string',
          minLength: 1,
          maxLength: 500,
            description:
            'English search query. Examples: "ongoing monitoring customer risk profile", "PEP enhanced due diligence", "EBA/GL/2021/02". The corpus is English and the default local embedding model is optimized for English; use focused regulatory terms rather than broad questions.',
        },
        filters: {
          type: 'object',
          additionalProperties: false,
          properties: FILTER_PROPERTIES,
          description: 'Optional exact-match filters. Example: {"eba_id":"EBA/GL/2021/02","document_type":"guidelines","publication_status":"final","topic":"AML/CFT"}.',
        },
        limit: { type: 'number', minimum: 1, maximum: 50, description: 'Max results (default 10)', default: 10 },
        include_context: { type: 'boolean', description: 'Include one neighboring chunk before and after each hit. Use when a citation appears to be a continuation of adjacent paragraphs.', default: false },
      },
      required: ['query'],
    },
  },
  {
    name: 'eba_get_document',
    description: 'Get document-level metadata and first citation chunks by official EBA ID. Use eba_get_toc for outline navigation or eba_get_section for full section retrieval; this tool is not a full-document dump.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN, description: 'Official EBA document ID (e.g. EBA/GL/2021/02 or EBA/LARGE-GL/2022/1)' },
        language: { type: 'string', enum: ['en'], description: 'Language code', default: 'en' },
      },
      required: ['eba_id'],
    },
  },
  {
    name: 'eba_get_paragraph',
    description: 'Get chunks for an exact paragraph_ref in one EBA document, with optional surrounding context. Use after eba_search when a result has paragraph_ref. If search returned paragraph_ref:null, this tool cannot navigate to that unnumbered chunk; use eba_get_section or eba_validate_citation instead.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN },
        paragraph_ref: { type: 'string', minLength: 1, maxLength: 50, pattern: PARAGRAPH_REF_PATTERN },
        language: { type: 'string', enum: ['en'], default: 'en' },
        context_before: { type: 'number', minimum: 0, maximum: 3, default: 0 },
        context_after: { type: 'number', minimum: 0, maximum: 3, default: 0 },
      },
      required: ['eba_id', 'paragraph_ref'],
    },
  },
  {
    name: 'eba_get_section',
    description:
      'Return citation chunks for a numbered section or paragraph-prefix in one EBA document, e.g. section "4" returns chunks with paragraph_ref 4, 4.1, 4.2 etc. Quick navigation tool for reading a whole regulatory section after eba_get_toc or eba_search. Best-effort: depends on parsed paragraph_ref/section_path metadata and may miss malformed PDF headings.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN, description: 'Official EBA document ID (e.g. EBA/GL/2021/02)' },
        section: { type: 'string', minLength: 1, maxLength: 80, pattern: SECTION_REF_PATTERN, description: 'Section or paragraph prefix to retrieve, e.g. "4", "4.7", "Title I", "Definitions".' },
        language: { type: 'string', enum: ['en'], default: 'en' },
        limit: { type: 'number', minimum: 1, maximum: 300, default: 200, description: 'Maximum chunks to return. Increase for long sections; citations are truncated to 500 characters each.' },
      },
      required: ['eba_id', 'section'],
    },
  },
  {
    name: 'eba_get_toc',
    description:
      'Return a best-effort outline for one EBA document: section_path entries with paragraph ranges, page ranges, sequence ranges, and chunk counts. Use before eba_get_section when you need to understand document structure. The outline is derived from parsed headings and paragraph metadata, not a guaranteed PDF table-of-contents extraction.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN, description: 'Official EBA document ID (e.g. EBA/GL/2021/02)' },
        language: { type: 'string', enum: ['en'], default: 'en' },
        limit: { type: 'number', minimum: 1, maximum: 300, default: 200, description: 'Maximum outline entries to return.' },
      },
      required: ['eba_id'],
    },
  },
  {
    name: 'eba_get_versions',
    description: 'Get available versions for a specific EBA document',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN, description: 'Official EBA document ID (e.g. EBA/GL/2021/02)' },
      },
      required: ['eba_id'],
    },
  },
  {
    name: 'eba_diff_versions',
    description: 'Compare metadata between two versions of a specific EBA document',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN, description: 'Official EBA document ID (e.g. EBA/GL/2021/02)' },
        version_a: { type: 'string', minLength: 1, maxLength: 100, pattern: VERSION_LABEL_PATTERN, description: 'First version label' },
        version_b: { type: 'string', minLength: 1, maxLength: 100, pattern: VERSION_LABEL_PATTERN, description: 'Second version label' },
      },
      required: ['eba_id', 'version_a', 'version_b'],
    },
  },
  {
    name: 'eba_list_documents',
    description: 'List all EBA documents in the corpus with optional filters',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        filters: {
          type: 'object',
          additionalProperties: false,
          properties: {
            document_type: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
            topic: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
            publication_status: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
            applicability_status: { type: 'string', maxLength: 80, pattern: FILTER_STRING_PATTERN },
            language: { type: 'string', enum: ['en'] },
          },
        },
        limit: { type: 'number', minimum: 1, maximum: 100, default: 20 },
      },
    },
  },
  {
    name: 'eba_corpus_info',
    description: 'Get information about the EBA corpus (document count, chunk count, version)',
    inputSchema: { type: 'object', additionalProperties: false, properties: {} },
  },
  {
    name: 'eba_get_status',
    description: 'Get publication and applicability status for a specific EBA document',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        eba_id: { type: 'string', maxLength: 40, pattern: EBA_ID_PATTERN, description: 'Official EBA document ID (e.g. EBA/GL/2021/02)' },
      },
      required: ['eba_id'],
    },
  },
  {
    name: 'eba_validate_citation',
    description: 'Validate a citation chunk ID — check if it exists and return document status metadata',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        chunk_id: { type: 'string', minLength: 1, maxLength: 240, pattern: CHUNK_ID_PATTERN, description: 'Chunk ID to validate (e.g. EBA-GL-2021-02:001921c3:en:p:seq-527)' },
      },
      required: ['chunk_id'],
    },
  },
] as const;

export async function createServer(): Promise<Server> {
  const server = new Server(
    { name: 'eba-mcp', version: '0.1.0' },
    { capabilities: { tools: {} } },
  );

  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: TOOLS }));

  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    let result: unknown;

    try {
        switch (name) {
        case 'eba_search':
          result = await handleEbaSearch(EbaSearchInput.parse(args));
          break;
        case 'eba_get_document':
          result = await handleEbaGetDocument(EbaGetDocumentInput.parse(args));
          break;
        case 'eba_get_paragraph':
          result = handleEbaGetParagraph(EbaGetParagraphInput.parse(args));
          break;
        case 'eba_get_section':
          result = handleEbaGetSection(EbaGetSectionInput.parse(args));
          break;
        case 'eba_get_toc':
          result = handleEbaGetToc(EbaGetTocInput.parse(args));
          break;
        case 'eba_get_versions':
          result = handleEbaGetVersions(EbaGetVersionsInput.parse(args));
          break;
        case 'eba_diff_versions':
          result = handleEbaDiffVersions(EbaDiffVersionsInput.parse(args));
          break;
        case 'eba_list_documents':
          result = handleEbaListDocuments(EbaListDocumentsInput.parse(args || {}));
          break;
        case 'eba_corpus_info':
          result = handleEbaCorpusInfo(EbaCorpusInfoInput?.parse(args || {}));
          break;
        case 'eba_get_status':
          result = handleEbaGetStatus(EbaGetStatusInput.parse(args));
          break;
        case 'eba_validate_citation':
          result = handleEbaValidateCitation(EbaValidateCitationInput.parse(args));
          break;
        default:
          throw new Error(`Unknown tool: ${name}`);
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unknown tool error';

      result = {
        answerability: 'error',
        error: message,
        citations: [],
        warnings: [message],
        query_trace_id: '',
      };
    }

    return {
      content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
    };
  });

  return server;
}

export async function startServer(dbPath: string): Promise<void> {
  initDb(dbPath);
  const server = await createServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}
