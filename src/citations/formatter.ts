import type { Chunk } from '../db/types.js';
import { formatCitationString } from './citation.js';

export interface CitationObject {
  citation_id: string;
  eba_id: string;
  paragraph_ref: string | null;
  section_path: string;
  page_start: number | null;
  page_end: number | null;
  text: string;
  citation: string;
  chunk_type: string;
}

export function buildCitation(chunk: Chunk, ebaId: string): CitationObject {
  return {
    citation_id: chunk.chunk_id,
    eba_id: ebaId,
    paragraph_ref: chunk.paragraph_ref || null,
    section_path: chunk.section_path || '',
    page_start: chunk.page_start || null,
    page_end: chunk.page_end || null,
    text: chunk.text.slice(0, 500),
    citation: formatCitationString(chunk, ebaId),
    chunk_type: chunk.chunk_type,
  };
}

export function buildCitations(chunks: Chunk[], ebaId: string): CitationObject[] {
  return chunks.map(c => buildCitation(c, ebaId || c.eba_id || ''));
}
