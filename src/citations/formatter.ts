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
  truncated: boolean;
  truncation_offset: string | null;
  citation: string;
  chunk_type: string;
}

export function buildCitation(chunk: Chunk, ebaId: string): CitationObject {
  const truncated = chunk.text.length > 500;
  const text = chunk.text.replace(/\n/g, ' ').slice(0, 500);

  return {
    citation_id: chunk.chunk_id,
    eba_id: ebaId,
    paragraph_ref: chunk.paragraph_ref || null,
    section_path: chunk.section_path || '',
    page_start: chunk.page_start || null,
    page_end: chunk.page_end || null,
    text,
    truncated,
    truncation_offset: truncated ? `500 / ${chunk.text.length}` : null,
    citation: formatCitationString(chunk, ebaId),
    chunk_type: chunk.chunk_type,
  };
}

export function buildCitations(chunks: Chunk[], ebaId: string): CitationObject[] {
  return chunks.map(c => buildCitation(c, ebaId || c.eba_id || ''));
}
