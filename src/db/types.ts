export interface Document {
  eba_id: string;
  title: string;
  document_type: string;
  topic: string;
  language: string;
  publication_url: string;
  published_at: string | null;
  applicability_status: string;
  publication_status: string;
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
}

export interface SearchFilters {
  document_type?: string;
  topic?: string;
  publication_status?: string;
  applicability_status?: string;
  language?: string;
  eba_id?: string;
}
