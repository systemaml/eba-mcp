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
  embedding_model: string;
  embedding_dim: number;
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

export interface TocEntry {
  section_path: string;
  paragraph_refs: string[];
  first_paragraph_ref: string | null;
  last_paragraph_ref: string | null;
  page_start: number | null;
  page_end: number | null;
  first_sequence_no: number;
  last_sequence_no: number;
  chunk_count: number;
}
