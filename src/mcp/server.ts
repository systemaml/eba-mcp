import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';

import { initDb } from '../db/sqlite.js';
import {
  EbaCorpusInfoInput,
  EbaDiffVersionsInput,
  EbaGetDocumentInput,
  EbaGetParagraphInput,
  EbaGetStatusInput,
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
  handleEbaGetStatus,
  handleEbaGetVersions,
  handleEbaListDocuments,
  handleEbaSearch,
  handleEbaValidateCitation,
} from './tools.js';

const EBA_ID_PATTERN = '^EBA/[A-Za-z][A-Za-z-]*/\\d{4}/\\d+$';
const CHUNK_ID_PATTERN = '^[A-Za-z0-9][A-Za-z0-9:_-]*$';
const PARAGRAPH_REF_PATTERN = '^[A-Za-z0-9][A-Za-z0-9 ._/-]*$';
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
      'Discover citation-ready excerpts from the English EBA corpus. Use English regulatory terms; split broad compliance/legal questions into several focused searches. The MCP returns excerpts and citations, not legal advice; synthesize answers only from returned citations.',
    inputSchema: {
      type: 'object',
      additionalProperties: false,
      properties: {
        query: {
          type: 'string',
          minLength: 1,
          maxLength: 500,
          description:
            'English search query. The corpus is English and the default local embedding model is optimized for English; use focused regulatory terms rather than broad questions.',
        },
        filters: {
          type: 'object',
          additionalProperties: false,
          properties: FILTER_PROPERTIES,
          description: 'Optional filters',
        },
        limit: { type: 'number', minimum: 1, maximum: 50, description: 'Max results (default 10)', default: 10 },
        include_context: { type: 'boolean', description: 'Include neighboring chunks', default: false },
      },
      required: ['query'],
    },
  },
  {
    name: 'eba_get_document',
    description: 'Get a specific EBA document by its official ID',
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
    description: 'Get a specific paragraph from an EBA document with optional context',
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
