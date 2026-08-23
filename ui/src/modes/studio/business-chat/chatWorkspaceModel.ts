import type { CloudFileRef, IntegrationConnectionInfo, WorkflowFileReference } from '../../../api/types';
import type { CollectionResource, DocumentResource } from '../../../api/knowledge';
import type { AssistantSegment, AgentActivity } from './businessChatModel';

export type WorkspaceSource = {
  id: string;
  title: string;
  subtitle?: string;
  kind: 'collection' | 'document' | 'upload' | 'drive' | 'web' | 'text' | 'code' | 'repository' | 'image' | 'saved';
  selected: boolean;
  status: 'uploading' | 'processing' | 'indexing' | 'ready' | 'synced' | 'refreshing' | 'failed' | 'outdated' | 'disabled' | 'unavailable';
  referenced?: boolean;
  accessed?: boolean;
  origin?: string;
  sizeBytes?: number;
  pageCount?: number;
  wordCount?: number;
  lineCount?: number;
  updatedAt?: string;
  thumbnailUrl?: string;
  collectionId?: string;
  documentId?: string;
  file?: WorkflowFileReference;
  sourceUrl?: string;
};

export function collectionAsSource(collection: CollectionResource): WorkspaceSource {
  return {
    id: `collection:${collection.collection_id}`,
    title: collection.name,
    subtitle: `${collection.document_count} documents · ${collection.status}`,
    kind: 'collection',
    selected: true,
    status: collection.status === 'ready' || collection.status === 'active'
      ? 'ready'
      : collection.status === 'failed' ? 'failed' : 'processing',
    collectionId: collection.collection_id,
  };
}

export type WorkspaceNote = {
  id: string;
  title: string;
  body: string;
  createdAt: string;
  updatedAt: string;
};

export type CitationTarget = Extract<AssistantSegment, { kind: 'sources' }>['items'][number];

export type CreateArtifactKind = 'report' | 'presentation' | 'brief' | 'table' | 'mind-map';

export const CREATE_OPTIONS: Array<{ id: CreateArtifactKind; label: string; description: string; icon: string }> = [
  { id: 'report', label: 'Report', description: 'A structured, cited analysis', icon: '▤' },
  { id: 'presentation', label: 'Presentation', description: 'An executive slide deck', icon: '▣' },
  { id: 'brief', label: 'Executive brief', description: 'A concise decision-ready summary', icon: '≡' },
  { id: 'table', label: 'Data table', description: 'Comparable findings in rows and columns', icon: '▦' },
  { id: 'mind-map', label: 'Mind map', description: 'Topics and relationships at a glance', icon: '⌘' },
];

export function documentsAsSources(
  documents: DocumentResource[],
  collectionId: string,
  selectedIds: Set<string>,
): WorkspaceSource[] {
  return documents.map(document => ({
    id: `document:${document.document_id}`,
    title: document.filename,
    subtitle: document.source_format || document.mime_type,
    kind: 'document',
    selected: selectedIds.has(`document:${document.document_id}`),
    status: document.status === 'ready' || document.status === 'active'
      ? 'ready'
      : document.status === 'failed' ? 'failed' : 'processing',
    collectionId,
    documentId: document.document_id,
  }));
}

export function uploadsAsSources(files: WorkflowFileReference[]): WorkspaceSource[] {
  return files.map(file => ({
    id: `upload:${file.file_id}`,
    title: file.name,
    subtitle: file.category,
    kind: 'upload',
    selected: true,
    status: 'ready',
    file,
  }));
}

export function driveFilesAsSources(
  files: WorkflowFileReference[],
  picked: CloudFileRef[],
  connection: IntegrationConnectionInfo,
): WorkspaceSource[] {
  return files.map((file, index) => {
    const cloudFile = picked[index];
    return {
      id: `drive:${connection.id}:${cloudFile?.id ?? file.file_id}`,
      title: cloudFile?.name ?? file.name,
      subtitle: `Google Drive · ${connection.address || connection.display_name}`,
      kind: 'drive',
      selected: true,
      status: 'synced',
      origin: connection.display_name || 'Google Drive',
      sizeBytes: cloudFile?.sizeBytes ?? file.size_bytes,
      updatedAt: cloudFile?.modifiedAt ?? undefined,
      sourceUrl: cloudFile?.webUrl ?? undefined,
      file,
    };
  });
}

export function httpUrlsInText(text: string): string[] {
  const matches = text.match(/https?:\/\/[^\s<>"']+/gi) ?? [];
  const normalized = matches.flatMap(value => {
    const candidate = value.replace(/[),.;!?]+$/g, '');
    try {
      const url = new URL(candidate);
      return url.protocol === 'http:' || url.protocol === 'https:' ? [url.toString()] : [];
    } catch {
      return [];
    }
  });
  return [...new Set(normalized)];
}

export function webSourcesFromText(text: string): WorkspaceSource[] {
  return httpUrlsInText(text).map(sourceUrl => {
    const url = new URL(sourceUrl);
    const path = url.pathname === '/' ? '' : url.pathname.replace(/\/$/, '');
    return {
      id: `web:${sourceUrl}`,
      title: `${url.hostname}${path}`,
      subtitle: 'Web source · used with this request',
      kind: 'web',
      selected: true,
      status: 'ready',
      sourceUrl,
    };
  });
}

export function selectedFiles(sources: WorkspaceSource[]): WorkflowFileReference[] {
  return sources.flatMap(source => source.selected && source.file ? [source.file] : []);
}

export function selectedCollectionId(sources: WorkspaceSource[]): string | null {
  const selectedCollectionRows = sources.filter(source => source.kind === 'collection' && source.selected && source.collectionId);
  if (selectedCollectionRows.length === 1) return selectedCollectionRows[0].collectionId ?? null;
  if (selectedCollectionRows.length > 1) return null;
  const ids = [...new Set(sources.flatMap(source => (
    source.kind === 'document' && source.selected && source.collectionId ? [source.collectionId] : []
  )))];
  return ids.length === 1 ? ids[0] : null;
}

export function selectedKnowledgeDocumentIds(sources: WorkspaceSource[]): string[] {
  return sources.flatMap(source => (
    source.kind === 'document' && source.selected && source.documentId ? [source.documentId] : []
  ));
}

export function selectedSourceCount(sources: WorkspaceSource[]): number {
  return sources.filter(source => source.selected).length;
}

export function sourceSelectable(source: WorkspaceSource): boolean {
  return source.status === 'ready' || source.status === 'synced' || source.status === 'outdated';
}

export function sourceStatusLabel(source: WorkspaceSource): string {
  const labels: Record<WorkspaceSource['status'], string> = {
    uploading: 'Uploading', processing: 'Processing', indexing: 'Indexing', ready: 'Ready', synced: 'Synced',
    refreshing: 'Refreshing', failed: 'Failed', outdated: 'Outdated', disabled: 'Disabled', unavailable: 'Unavailable',
  };
  return labels[source.status];
}

export function sourceKindLabel(kind: WorkspaceSource['kind']): string {
  const labels: Record<WorkspaceSource['kind'], string> = {
    collection: 'Collection', document: 'Document', upload: 'Upload', drive: 'Drive', web: 'Web', text: 'Text',
    code: 'Code', repository: 'Repository', image: 'Image', saved: 'Saved source',
  };
  return labels[kind];
}

export function activitySummary(activities: AgentActivity[]): string {
  const active = activities.find(item => item.status === 'needs_input')
    ?? activities.find(item => item.status === 'running')
    ?? [...activities].reverse().find(item => item.status === 'failed')
    ?? [...activities].reverse().find(item => item.status === 'completed');
  if (!active) return 'Ready';
  if (active.status === 'needs_input') return 'Waiting for your input';
  if (active.status === 'running') return active.text || 'Working…';
  if (active.status === 'failed') return 'Something needs attention';
  return 'Complete';
}

export type FriendlyError = {
  title: string;
  message: string;
  actions: string[];
};

export function friendlyError(error: string): FriendlyError {
  const value = error.toLowerCase();
  if (value.includes('retrieval_timeout') || (value.includes('retrieval') && value.includes('deadline'))) {
    return {
      title: 'Knowledge search took too long',
      message: 'Your sources and conversation are safe. Retry the Knowledge search; completed workflow steps can be reused when a checkpoint is available.',
      actions: ['Retry Knowledge search'],
    };
  }
  if (value.includes('multiple') || value.includes('ambiguous') || value.includes('unique')) {
    return { title: 'We found more than one possible match', message: 'Choose the correct record, search again, or continue without that connected data.', actions: ['Select manually', 'Search again', 'Continue without it'] };
  }
  if (value.includes('source') || value.includes('document') || value.includes('collection')) {
    return { title: 'A source could not be read', message: 'Check that the source is ready and still available, then try again.', actions: ['Review sources', 'Try again'] };
  }
  if (value.includes('connection') || value.includes('mcp') || value.includes('tool')) {
    return { title: 'A connected service is unavailable', message: 'The request can be retried, or you can continue without the connected service.', actions: ['Try again', 'Continue without it'] };
  }
  if (value.includes('citation') || value.includes('evidence')) {
    return { title: 'There was not enough supporting evidence', message: 'Choose more sources or broaden the question before trying again.', actions: ['Choose sources', 'Ask a broader question'] };
  }
  return { title: 'We could not complete that request', message: 'Your sources and conversation are safe. Try again or inspect the technical details.', actions: ['Try again'] };
}

export function artifactPrompt(kind: CreateArtifactKind, focus: string, detailed: boolean): string {
  const label: Record<CreateArtifactKind, string> = {
    report: 'a research report', presentation: 'an executive presentation', brief: 'an executive brief',
    table: 'a comparison table', 'mind-map': 'a mind map',
  };
  return `Create ${label[kind]} from the selected sources.${focus.trim() ? ` Focus on: ${focus.trim()}.` : ''} ${detailed ? 'Make it detailed and comprehensive.' : 'Keep it concise and decision-ready.'} Include citations where supported.`;
}