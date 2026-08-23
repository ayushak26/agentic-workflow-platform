import { apiBase, getAuthHeaders } from './client';

const API = `${apiBase()}/api`;

function afetch(input: string, init: RequestInit = {}): Promise<Response> {
  return fetch(input, { ...init, credentials: 'include' });
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const text = await r.text();
    try {
      const payload = JSON.parse(text);
      const detail = payload?.detail ?? payload;
      throw new Error(`${r.status} ${typeof detail === 'string' ? detail : JSON.stringify(detail)}`);
    } catch {
      throw new Error(`${r.status} ${text}`);
    }
  }
  return r.json() as Promise<T>;
}

function jsonHeaders(): Record<string, string> {
  return getAuthHeaders({ 'content-type': 'application/json' });
}

// ---- Resource types (mirror app/knowledge/models.py) ----

export type DocTypeChoice = {
  id: string; label: string; description: string; precision_sensitive: boolean;
};

export type EmbeddingModelChoice = {
  id: string; label: string; dimensions: number; provider: string; verified: boolean; note: string;
};

export type ResourceStatus = 'draft' | 'building' | 'ready' | 'active' | 'inactive' | 'failed' | 'archived';
export type ProfileType = 'parser' | 'chunking' | 'embedding' | 'retrieval' | 'routing' | 'reranker' | 'generation';

export type CollectionResource = {
  collection_id: string;
  name: string;
  description: string;
  status: ResourceStatus;
  document_count: number;
  chunk_count: number;
  active_index_id: string | null;
  metadata_schema: Record<string, unknown>;
  doc_types: string[];
};

export type ProfileVersion = {
  profile_id: string;
  profile_type: ProfileType;
  name: string;
  version: number;
  strategy: string;
  config: Record<string, unknown>;
  description: string;
  status: ResourceStatus;
};

export type IndexVersion = {
  index_id: string;
  collection_id: string;
  version: number;
  parser_profile_id: string;
  parser_profile_version: number;
  chunking_profile_id: string;
  chunking_profile_version: number;
  embedding_profile_id: string;
  embedding_profile_version: number;
  status: ResourceStatus;
  document_count: number;
  chunk_count: number;
  activated_at: string | null;
};

export type IngestionJobStatus =
  | 'queued' | 'uploading' | 'parsing' | 'chunking' | 'enriching'
  | 'embedding' | 'indexing' | 'completed' | 'partially_completed' | 'failed' | 'cancelled';

export type IngestionJob = {
  ingestion_job_id: string;
  collection_id: string;
  parser_profile_id: string;
  chunking_profile_id: string;
  embedding_profile_id: string;
  target_index_id: string;
  status: IngestionJobStatus;
  documents_total: number;
  documents_processed: number;
  documents_failed: number;
  chunks_created: number;
  current_document_id: string | null;
  errors: Array<{ document_id: string | null; filename: string; error_type: string; message: string }>;
};

export type DocumentResource = {
  document_id: string;
  collection_id: string;
  filename: string;
  mime_type: string;
  source_format: string;
  status: ResourceStatus;
  error: string | null;
};

export type RAGAgentDefinition = {
  rag_agent_id: string;
  name: string;
  description: string;
  collection_id: string;
  retrieval_profile_id: string;
  generation_profile_id: string;
  routing_profile_id: string | null;
  status: ResourceStatus;
};

export type RAGCitation = {
  label: number;
  chunk_id: string;
  filename: string;
  page: number | null;
  section: string | null;
  snippet: string;
  evidence_status: string;
};

export type RAGSource = {
  file_name: string;
  document_id: string | null;
  source_id: string | null;
  source_version_id: string | null;
  metadata: Record<string, unknown>;
  locations: Array<{ page: number | null; section: string | null }>;
};

export type RAGRelevantContextItem = {
  content: string;
  score: number | null;
  file_name: string;
  page_no: number | null;
  section: string | null;
};

export type RAGQueryResponse = {
  request_id: string;
  rag_agent_id: string;
  collection_id: string;
  index_id: string;
  retrieval_profile_id: string;
  generation_profile_id: string;
  query: string;
  answer: string;
  citations: RAGCitation[];
  sources: RAGSource[];
  relevant_context: RAGRelevantContextItem[];
  configured_answering_model: string;
  retrieved_chunks: Array<Record<string, unknown>>;
  retrieval_trace_id: string;
  candidate_count: number;
  context_count: number;
  timings_ms: Record<string, number>;
  resolved_resources: Record<string, unknown>;
  generation: Record<string, unknown>;
};

export type RetrievedChunkView = {
  chunk_id: string;
  doc_title: string;
  text: string;
  page: number | null;
  dense_score?: number | null;
  sparse_score?: number | null;
  fusion_score?: number | null;
  hybrid_score?: number | null;
  rerank_score?: number | null;
};

export type RetrievalStageView = {
  name: string;
  duration_ms: number;
  input_count: number | null;
  output_count: number | null;
  details: Record<string, unknown>;
};

export type RetrievalResult = {
  query: string;
  strategy: string;
  retrieval_request_id: string;
  chunks: RetrievedChunkView[];
  candidates: RetrievedChunkView[];
  stages: RetrievalStageView[];
  final_context: string;
  context_token_count: number;
  timings_ms: Record<string, number>;
};

export type RetrievalPreset = {
  name: string;
  strategy: string;
  config: Record<string, unknown>;
};

export type RetrievalTraceSummary = {
  retrieval_request_id: string;
  original_query: string;
  collection_id: string;
  resolved_index_id: string;
  status: string;
  created_at: string;
  timings_ms: Record<string, number>;
};

export type RetrievalChunkContext = {
  retrieval_request_id: string;
  current: { chunk_id: string; document_id: string | null; title: string; text: string; page: number | null; section: string | null };
  previous: { chunk_id: string; document_id: string | null; title: string; text: string; page: number | null; section: string | null } | null;
  next: { chunk_id: string; document_id: string | null; title: string; text: string; page: number | null; section: string | null } | null;
};

// ---- Ingestion preset shape returned by GET /api/knowledge/ingestion-presets ----

export type IngestionPreset = {
  name: string;
  parser: { strategy: string; config: Record<string, unknown> };
  chunking: { strategy: string; config: Record<string, unknown> };
  enrichment: { prepend_context: boolean };
};

export type ProfileCreateRequest = {
  profile_type: ProfileType;
  name: string;
  strategy: string;
  config?: Record<string, unknown>;
  description?: string;
};

export const knowledgeApi = {
  // ---- Collections ----
  listCollections: (): Promise<CollectionResource[]> =>
    afetch(`${API}/knowledge/collections`, { headers: getAuthHeaders() }).then(r => j<CollectionResource[]>(r)),

  getCollection: (collectionId: string): Promise<CollectionResource> =>
    afetch(`${API}/knowledge/collections/${collectionId}`, { headers: getAuthHeaders() }).then(r => j<CollectionResource>(r)),

  createCollection: (payload: {
    name: string;
    description?: string;
    metadata_schema?: Record<string, unknown>;
    doc_types?: string[];
  }): Promise<CollectionResource> =>
    afetch(`${API}/knowledge/collections`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify(payload),
    }).then(r => j<CollectionResource>(r)),

  // ---- Profiles ----
  createProfile: (payload: ProfileCreateRequest): Promise<ProfileVersion> =>
    afetch(`${API}/knowledge/profiles`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify(payload),
    }).then(r => j<ProfileVersion>(r)),

  listProfiles: (profileType?: string): Promise<ProfileVersion[]> =>
    afetch(`${API}/knowledge/profiles${profileType ? `?profile_type=${profileType}` : ''}`, {
      headers: getAuthHeaders(),
    }).then(r => j<ProfileVersion[]>(r)),

  getProfile: (profileId: string, version?: number): Promise<ProfileVersion> =>
    afetch(`${API}/knowledge/profiles/${profileId}${version ? `?version=${version}` : ''}`, {
      headers: getAuthHeaders(),
    }).then(r => j<ProfileVersion>(r)),

  defaults: (): Promise<Record<ProfileType, ProfileVersion>> =>
    afetch(`${API}/knowledge/profiles/defaults`, { method: 'POST', headers: getAuthHeaders() })
      .then(r => j<Record<ProfileType, ProfileVersion>>(r)),

  ingestionPresets: (): Promise<Record<string, IngestionPreset>> =>
    afetch(`${API}/knowledge/ingestion-presets`, { headers: getAuthHeaders() })
      .then(r => j<Record<string, IngestionPreset>>(r)),

  // ---- Indexes ----
  listIndexes: (collectionId: string): Promise<IndexVersion[]> =>
    afetch(`${API}/knowledge/collections/${collectionId}/indexes`, { headers: getAuthHeaders() })
      .then(r => j<IndexVersion[]>(r)),

  activateIndex: (collectionId: string, indexId: string): Promise<CollectionResource> =>
    afetch(`${API}/knowledge/collections/${collectionId}/indexes/${indexId}/activate`, {
      method: 'POST', headers: getAuthHeaders(),
    }).then(r => j<CollectionResource>(r)),

  // ---- Ingestion ----
  startIngestion: (
    collectionId: string,
    files: File[],
    profiles: { parser: ProfileVersion; chunking: ProfileVersion; embedding?: ProfileVersion; embeddingModel?: string },
    metadata: Record<string, unknown> = {},
  ): Promise<IngestionJob> => {
    const form = new FormData();
    files.forEach(file => form.append('files', file));
    form.append('parser_profile_id', profiles.parser.profile_id);
    form.append('parser_profile_version', String(profiles.parser.version));
    form.append('chunking_profile_id', profiles.chunking.profile_id);
    form.append('chunking_profile_version', String(profiles.chunking.version));
    // Either pin an existing Embedding Profile, or name a model (including
    // 'auto') and let the backend resolve and pin it.
    if (profiles.embeddingModel) {
      form.append('embedding_model', profiles.embeddingModel);
    } else if (profiles.embedding) {
      form.append('embedding_profile_id', profiles.embedding.profile_id);
      form.append('embedding_profile_version', String(profiles.embedding.version));
    }
    form.append('metadata_json', JSON.stringify(metadata));
    return afetch(`${API}/knowledge/collections/${collectionId}/ingestions`, {
      method: 'POST', headers: getAuthHeaders(), body: form,
    }).then(r => j<IngestionJob>(r));
  },

  listIngestions: (collectionId?: string): Promise<IngestionJob[]> =>
    afetch(`${API}/knowledge/ingestions${collectionId ? `?collection_id=${collectionId}` : ''}`, {
      headers: getAuthHeaders(),
    }).then(r => j<IngestionJob[]>(r)),

  getJob: (jobId: string): Promise<IngestionJob> =>
    afetch(`${API}/knowledge/ingestions/${jobId}`, { headers: getAuthHeaders() }).then(r => j<IngestionJob>(r)),

  cancelIngestion: (jobId: string): Promise<IngestionJob> =>
    afetch(`${API}/knowledge/ingestions/${jobId}/cancel`, { method: 'POST', headers: getAuthHeaders() })
      .then(r => j<IngestionJob>(r)),

  // ---- Documents ----
  listDocuments: (collectionId: string): Promise<DocumentResource[]> =>
    afetch(`${API}/knowledge/collections/${collectionId}/documents`, { headers: getAuthHeaders() })
      .then(r => j<DocumentResource[]>(r)),

  documentSourceUrl: (documentId: string): Promise<{ url: string; expires_seconds: number }> =>
    afetch(`${API}/knowledge/documents/${documentId}/source-url`, { headers: getAuthHeaders() })
      .then(r => j<{ url: string; expires_seconds: number }>(r)),

  docTypes: (): Promise<{ doc_types: DocTypeChoice[] }> =>
    afetch(`${API}/knowledge/doc-types`, { headers: getAuthHeaders() })
      .then(r => j<{ doc_types: DocTypeChoice[] }>(r)),

  embeddingModels: (): Promise<{ models: EmbeddingModelChoice[]; configured_default: string; endpoint: string }> =>
    afetch(`${API}/knowledge/embedding-models`, { headers: getAuthHeaders() })
      .then(r => j<{ models: EmbeddingModelChoice[]; configured_default: string; endpoint: string }>(r)),

  // ---- Models ----
  // Generation models come from the platform registry (same list the Builder
  // uses). Embedding models are not in that registry — they are chosen per
  // Index at ingestion time, so EMBEDDING_MODEL_CHOICES below seeds the picker.
  llmModels: (): Promise<{ models: Array<{ name: string; display_name: string; provider: string; enabled?: boolean; configured?: boolean }> }> =>
    afetch(`${API}/llm/models`, { headers: getAuthHeaders() })
      .then(r => j<{ models: Array<{ name: string; display_name: string; provider: string; enabled?: boolean; configured?: boolean }> }>(r)),

  // ---- Retrieval Playground ----
  retrievalPresets: (): Promise<Record<string, RetrievalPreset>> =>
    afetch(`${API}/retrieval/presets`, { headers: getAuthHeaders() }).then(r => j<Record<string, RetrievalPreset>>(r)),

  search: (payload: Record<string, unknown>): Promise<RetrievalResult> =>
    afetch(`${API}/retrieval/search`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify(payload),
    }).then(r => j<RetrievalResult>(r)),

  compare: (
    experiments: Array<Record<string, unknown>>,
  ): Promise<{ results: RetrievalResult[]; pairwise_overlap: Array<{ left: number; right: number; shared_count: number; jaccard: number }> }> =>
    afetch(`${API}/retrieval/compare`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify({ experiments }),
    }).then(r => j<{ results: RetrievalResult[]; pairwise_overlap: Array<{ left: number; right: number; shared_count: number; jaccard: number }> }>(r)),

  // ---- Traces ----
  listTraces: (limit = 100): Promise<RetrievalTraceSummary[]> =>
    afetch(`${API}/retrieval/traces?limit=${limit}`, { headers: getAuthHeaders() }).then(r => j<RetrievalTraceSummary[]>(r)),

  getTrace: (retrievalRequestId: string): Promise<Record<string, unknown>> =>
    afetch(`${API}/retrieval/traces/${retrievalRequestId}`, { headers: getAuthHeaders() })
      .then(r => j<Record<string, unknown>>(r)),

  getTraceChunkContext: (retrievalRequestId: string, chunkId: string): Promise<RetrievalChunkContext> =>
    afetch(`${API}/retrieval/traces/${encodeURIComponent(retrievalRequestId)}/chunks/${encodeURIComponent(chunkId)}/context`, {
      headers: getAuthHeaders(),
    }).then(r => j<RetrievalChunkContext>(r)),

  // ---- RAG Agents ----
  createRagAgent: (payload: {
    name: string; description?: string; collection_id: string;
    retrieval_profile_id: string; generation_profile_id: string; routing_profile_id?: string | null;
  }): Promise<RAGAgentDefinition> =>
    afetch(`${API}/rag-agents`, {
      method: 'POST', headers: jsonHeaders(), body: JSON.stringify(payload),
    }).then(r => j<RAGAgentDefinition>(r)),

  listRagAgents: (search?: string): Promise<RAGAgentDefinition[]> =>
    afetch(`${API}/rag-agents${search ? `?search=${encodeURIComponent(search)}` : ''}`, {
      headers: getAuthHeaders(),
    }).then(r => j<RAGAgentDefinition[]>(r)),

  getRagAgent: (ragAgentId: string): Promise<RAGAgentDefinition> =>
    afetch(`${API}/rag-agents/${ragAgentId}`, { headers: getAuthHeaders() }).then(r => j<RAGAgentDefinition>(r)),

  queryRagAgent: (
    ragAgentId: string, query: string, runtimeFilters: Record<string, unknown> = {},
    runtimeContext: Record<string, unknown> = {},
  ): Promise<RAGQueryResponse> =>
    afetch(`${API}/rag-agents/${ragAgentId}/query`, {
      method: 'POST', headers: jsonHeaders(),
      body: JSON.stringify({ query, runtime_filters: runtimeFilters, runtime_context: runtimeContext }),
    }).then(r => j<RAGQueryResponse>(r)),
};
