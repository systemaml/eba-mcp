export interface Document {
  eba_id: string;
  title: string;
  document_type: string;
  topic: string;
  language: string;
  publication_url: string;
  published_at: string | null;
  application_date: string | null;
  applicability_status: string;
  publication_status: string;
  is_canonical: boolean;
}

export interface Chunk {
  chunk_id: string;
  document_version_id: number;
  language: string;
  section_path: string;
  paragraph_ref: string | null;
  page_start: number | null;
  page_end: number | null;
  section_ref?: string | null;
  section_title?: string | null;
  section_level?: number | null;
  parent_section_ref?: string | null;
  document_region?: string | null;
  metadata_confidence?: number | null;
  metadata_source?: string | null;
  text: string;
  text_hash: string;
  chunk_type: string;
  sequence_no: number;
  eba_id?: string;
  title?: string;
}

export interface CorpusManifest {
  manifest_hash: string;
  built_at: string;
  document_count: number;
  chunk_count: number;
  embedding_model?: string | null;
  embedding_dim?: number | null;
}

export interface SearchFilters {
  document_type?: string;
  topic?: string;
  publication_status?: string;
  applicability_status?: string;
  language?: string;
  eba_id?: string;
  exclude_consultation_responses?: boolean;
}

export type TocConfidence = 'high' | 'medium' | 'low';

export interface TocEntry {
  section_path: string;
  section_ref?: string;
  level?: number;
  parent_section_ref?: string | null;
  confidence?: TocConfidence;
  paragraph_refs: string[];
  first_paragraph_ref: string | null;
  last_paragraph_ref: string | null;
  page_start: number | null;
  page_end: number | null;
  first_sequence_no: number;
  last_sequence_no: number;
  chunk_count: number;
}

export interface PersistedTocEntry {
  document_version_id: number;
  section_ref: string;
  title: string;
  level: number;
  parent_section_ref: string | null;
  page_start: number | null;
  page_end: number | null;
  sequence_start: number | null;
  sequence_end: number | null;
  confidence: number | null;
  source: string | null;
}
