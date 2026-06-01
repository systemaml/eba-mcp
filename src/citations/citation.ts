import type { Chunk } from '../db/types.js';

export function formatCitationString(chunk: Chunk, ebaId: string): string {
  const ref = chunk.paragraph_ref
    ? `para. ${chunk.paragraph_ref}`
    : `section "${(chunk.section_path || 'unknown').slice(0, 60)}"`;
  const pageRef = chunk.page_start != null ? `, p. ${chunk.page_start}` : '';
  return `${ebaId}, ${ref}${pageRef}`;
}
