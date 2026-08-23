import { useEffect, useRef, useState } from 'react';
import { api } from '../../../api/client';
import { knowledgeApi, type CollectionResource, type DocumentResource, type RetrievalChunkContext } from '../../../api/knowledge';
import type { CloudFileRef, IntegrationConnectionInfo } from '../../../api/types';
import { CloudFileBrowser } from '../builder/CloudFileBrowser';
import type { AgentActivity } from './businessChatModel';
import { artifactPrompt, CREATE_OPTIONS, type CitationTarget, type CreateArtifactKind, type WorkspaceNote } from './chatWorkspaceModel';
import type { LocalChatRecord } from './chatWorkspaceStorage';

export type ExistingChatWorkflow = {
  id: string;
  source: 'private' | 'shared';
  title: string;
  description: string;
  status: string;
};

function Drawer({ title, onClose, children, wide = false }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  const dialogRef = useRef<HTMLElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    const dialog = dialogRef.current;
    const first = dialog?.querySelector<HTMLElement>('button, a[href], input, textarea, select, [tabindex]:not([tabindex="-1"])');
    (first ?? dialog)?.focus();
    return () => restoreFocusRef.current?.focus();
  }, []);
  return <div className="chat-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><section ref={dialogRef} tabIndex={-1} className={`chat-drawer ${wide ? 'chat-drawer--wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); onClose(); return; }
    if (event.key !== 'Tab') return;
    const focusable = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? [])];
    if (focusable.length === 0) { event.preventDefault(); dialogRef.current?.focus(); return; }
    const first = focusable[0]; const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  }}><header><h2>{title}</h2><button type="button" onClick={onClose} aria-label={`Close ${title}`}>×</button></header>{children}</section></div>;
}

export function ChatHistoryDrawer({ open, chats, activeChatId, onClose, onNew, onOpen, onRename, onDelete }: {
  open: boolean;
  chats: LocalChatRecord[];
  activeChatId: string;
  onClose: () => void;
  onNew: () => void;
  onOpen: (chat: LocalChatRecord) => void;
  onRename: (chat: LocalChatRecord) => void;
  onDelete: (chat: LocalChatRecord) => void;
}) {
  if (!open) return null;
  return <Drawer title="Chats" onClose={onClose}><div className="chat-history"><p className="chat-history-local">Stored only in this browser. Workflow conversations remain backed by the existing runtime.</p><button type="button" className="chat-history-new" onClick={onNew}>＋ New chat</button><div className="chat-history-list">{chats.map(chat => <div className={`chat-history-row ${chat.id === activeChatId ? 'is-active' : ''}`} key={chat.id}><button type="button" className="chat-history-open" onClick={() => onOpen(chat)}><strong>{chat.title}</strong><small>{chat.workflowId ? 'Conversation' : 'Draft'} · {new Date(chat.updatedAt).toLocaleDateString()}</small></button><button type="button" aria-label={`Rename ${chat.title}`} onClick={() => onRename(chat)}>Rename</button><button type="button" aria-label={`Delete ${chat.title}`} onClick={() => onDelete(chat)}>Delete</button></div>)}</div></div></Drawer>;
}

export function ExistingWorkflowDrawer({ open, workflows, loading, error, onClose, onOpen }: {
  open: boolean;
  workflows: ExistingChatWorkflow[];
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onOpen: (workflow: ExistingChatWorkflow) => void;
}) {
  const [query, setQuery] = useState('');
  useEffect(() => { if (open) setQuery(''); }, [open]);
  if (!open) return null;
  const normalized = query.trim().toLowerCase();
  const visible = workflows.filter(workflow => !normalized || `${workflow.title} ${workflow.description}`.toLowerCase().includes(normalized));
  return <Drawer title="Use existing workflow" onClose={onClose} wide><div className="chat-workflow-picker"><p>Builder workflows and private Chat workflows appear here. Workflow configuration stays in Workflows.</p><label>Search workflows<input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search by name or purpose…" /></label>{loading && <p className="chat-muted" role="status">Loading workflows…</p>}{error && <p className="chat-entry-error" role="alert">{error}</p>}<div className="chat-workflow-list">{visible.map(workflow => { const blocked = workflow.status === 'blocked'; return <button type="button" key={`${workflow.source}:${workflow.id}`} disabled={blocked} title={blocked ? 'Open this workflow in Builder and resolve its readiness errors before using it in Chat.' : undefined} onClick={() => onOpen(workflow)}><span><strong>{workflow.title}</strong><small>{blocked ? 'Blocked · fix readiness errors in Builder' : workflow.description || 'No description provided.'}</small></span><span>{workflow.source === 'private' ? 'Private' : 'Builder'}</span></button>; })}</div>{!loading && !error && visible.length === 0 && <p className="chat-muted">No workflows match this search.</p>}</div></Drawer>;
}

export function SourcePickerDialog({ open, sourceCount, knowledgeEnabled = false, onClose, onUpload, onAddUrls, onSelectCollection, onImportDrive }: {
  open: boolean;
  sourceCount: number;
  knowledgeEnabled?: boolean;
  onClose: () => void;
  onUpload: (files: File[]) => void | Promise<void>;
  onAddUrls: (text: string) => void;
  onSelectCollection?: (collection: CollectionResource, documents: DocumentResource[]) => void;
  onImportDrive: (connection: IntegrationConnectionInfo, files: CloudFileRef[]) => void | Promise<void>;
}) {
  const [collections, setCollections] = useState<CollectionResource[]>([]);
  const [connections, setConnections] = useState<IntegrationConnectionInfo[]>([]);
  const [selectedConnectionId, setSelectedConnectionId] = useState('');
  const [urlText, setUrlText] = useState('');
  const [activeMethod, setActiveMethod] = useState<'upload' | 'url' | 'drive' | 'knowledge' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const refreshConnections = () => api.integrationConnections().then(result => {
    const googleConnections = result.connections.filter(connection => connection.provider === 'google_drive');
    setConnections(googleConnections);
    setSelectedConnectionId(current => googleConnections.some(connection => connection.id === current) ? current : (googleConnections[0]?.id ?? ''));
  }).catch(() => setConnections([]));
  useEffect(() => {
    if (!open) return;
    setActiveMethod(null);
    setUrlText('');
    setError(null);
    void refreshConnections();
    if (knowledgeEnabled) void knowledgeApi.listCollections().then(setCollections).catch(() => setCollections([]));
  }, [knowledgeEnabled, open]);
  useEffect(() => {
    if (!open) return;
    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === 'integration-oauth-complete') void refreshConnections();
    };
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [open]);
  if (!open) return null;
  const chooseCollection = async (collection: CollectionResource) => {
    setBusy(true);
    setError(null);
    try { onSelectCollection?.(collection, await knowledgeApi.listDocuments(collection.collection_id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The collection could not be loaded.'); }
    finally { setBusy(false); }
  };
  const selectedConnection = connections.find(connection => connection.id === selectedConnectionId);
  const importDrive = async (files: CloudFileRef[]) => {
    if (!selectedConnection || files.length === 0) return;
    setBusy(true);
    setError(null);
    try { await onImportDrive(selectedConnection, files); setActiveMethod(null); }
    catch (reason) { setError(reason instanceof Error ? reason.message : 'The selected Drive files could not be imported.'); }
    finally { setBusy(false); }
  };
  return <Drawer title="Add sources" onClose={onClose} wide><div className="chat-source-picker">
    <div className="chat-source-picker-intro"><div><strong>Bring the right context into Chat</strong><p>Add local files, web pages, Google Drive documents or governed Knowledge.</p></div><span>{sourceCount} available</span></div>
    <div className="chat-picker-grid chat-picker-grid--methods">
      <label className="chat-picker-card"><input type="file" multiple className="sr-only" onChange={event => { const files = Array.from(event.target.files ?? []); event.target.value = ''; if (files.length) void onUpload(files); }} /><span>↑</span><strong>Upload files</strong><small>PDF, Office, data, images, text and code</small></label>
      <button type="button" className={`chat-picker-card ${activeMethod === 'url' ? 'is-active' : ''}`} onClick={() => setActiveMethod(activeMethod === 'url' ? null : 'url')}><span>⌁</span><strong>Website or URL</strong><small>Add one or several public web pages</small></button>
      <button type="button" className={`chat-picker-card ${activeMethod === 'drive' ? 'is-active' : ''}`} onClick={() => setActiveMethod(activeMethod === 'drive' ? null : 'drive')}><span>◇</span><strong>Google Drive</strong><small>Connect, browse and import documents securely</small></button>
      {knowledgeEnabled && <button type="button" className={`chat-picker-card ${activeMethod === 'knowledge' ? 'is-active' : ''}`} onClick={() => setActiveMethod(activeMethod === 'knowledge' ? null : 'knowledge')}><span>▦</span><strong>Your Knowledge</strong><small>Use a governed collection and its RAG Agent</small></button>}
    </div>
    {activeMethod === 'url' && <section className="chat-source-method" aria-label="Add website URLs"><div><h3>Website or URL</h3><p>Enter one URL per line. Chat records them as explicit web sources; it does not claim they were indexed.</p></div><textarea rows={4} value={urlText} onChange={event => setUrlText(event.target.value)} placeholder={'https://example.com/report\nhttps://example.com/research'} /><button type="button" className="is-primary" disabled={!urlText.trim()} onClick={() => { onAddUrls(urlText); setUrlText(''); setActiveMethod(null); }}>Add URLs</button></section>}
    {activeMethod === 'drive' && <section className="chat-source-method" aria-label="Add Google Drive files"><div className="chat-source-method-heading"><div><h3>Google Drive</h3><p>Read-only access. Selected files are imported into Chat’s protected workflow-file store.</p></div><button type="button" onClick={() => window.open(api.integrationConnectUrl('google_drive'), 'integration-oauth-connect', 'width=640,height=760')}>{connections.length ? 'Connect another account' : 'Connect Google Drive'}</button></div>{connections.length > 0 ? <><label>Account<select value={selectedConnectionId} onChange={event => setSelectedConnectionId(event.target.value)}>{connections.map(connection => <option key={connection.id} value={connection.id}>{connection.display_name}{connection.address ? ` · ${connection.address}` : ''}{connection.needs_reauth ? ' · reconnect required' : ''}</option>)}</select></label>{selectedConnection && !selectedConnection.needs_reauth && <CloudFileBrowser connectionId={selectedConnection.id} mode="file" multiple onSelect={files => void importDrive(files)} />}{selectedConnection?.needs_reauth && <p className="chat-entry-error">This account needs to be reconnected before its files can be browsed.</p>}</> : <div className="chat-drive-empty"><span>◇</span><strong>No Google Drive connected</strong><p>Connect an account to browse folders and search files without exposing credentials to Chat.</p></div>}</section>}
    {activeMethod === 'knowledge' && knowledgeEnabled && <section className="chat-source-method" aria-label="Add Knowledge collection"><div><h3>Your Knowledge</h3><p>Select a governed collection. Chat will resolve its active RAG Agent before running.</p></div><div className="chat-collection-picker">{collections.map(collection => <button type="button" key={collection.collection_id} disabled={busy} onClick={() => void chooseCollection(collection)}><span><strong>{collection.name}</strong><small>{collection.document_count} documents · {collection.status}</small></span><span>Choose</span></button>)}{collections.length === 0 && <p className="chat-muted">No ready Knowledge collections were found.</p>}</div></section>}
    {busy && <p className="chat-muted" role="status">Adding sources…</p>}
    {error && <p className="chat-entry-error" role="alert">{error}</p>}
  </div></Drawer>;
}

export function CitationDrawer({ citation, onClose, onSave, onAsk }: { citation: CitationTarget | null; onClose: () => void; onSave: (citation: CitationTarget) => void; onAsk: (citation: CitationTarget) => void }) {
  const [sourceUrl, setSourceUrl] = useState<string | null>(null);
  const [context, setContext] = useState<RetrievalChunkContext | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [contextError, setContextError] = useState<string | null>(null);
  useEffect(() => { setSourceUrl(citation?.sourceUri ?? null); if (citation?.documentId) void knowledgeApi.documentSourceUrl(citation.documentId).then(result => setSourceUrl(result.url)).catch(() => undefined); }, [citation]);
  useEffect(() => { setContext(null); setContextError(null); setContextLoading(false); }, [citation]);
  if (!citation) return null;
  const loadContext = async () => {
    if (!citation.retrievalTraceId || !citation.chunkId || contextLoading) return;
    setContextLoading(true); setContextError(null);
    try { setContext(await knowledgeApi.getTraceChunkContext(citation.retrievalTraceId, citation.chunkId)); }
    catch (reason) { setContextError(reason instanceof Error ? reason.message : 'Surrounding context is unavailable.'); }
    finally { setContextLoading(false); }
  };
  return <Drawer title={`Source ${citation.number}`} onClose={onClose}><div className="chat-citation-drawer"><h3>{citation.title}</h3><p className="chat-citation-location">{citation.page ? `Page ${citation.page}` : ''}{citation.page && citation.section ? ' · ' : ''}{citation.section}</p>{citation.snippet ? <blockquote>{citation.snippet}</blockquote> : <p className="chat-muted">No excerpt was included with this citation.</p>}<div className="chat-drawer-actions">{sourceUrl && <a href={sourceUrl} target="_blank" rel="noreferrer">Open source</a>}{citation.downloadUrl && <a href={citation.downloadUrl}>Download source</a>}{citation.retrievalTraceId && citation.chunkId && !context && <button type="button" disabled={contextLoading} onClick={() => void loadContext()}>{contextLoading ? 'Loading context…' : 'Show surrounding context'}</button>}<button type="button" onClick={() => onSave(citation)}>Save excerpt</button><button type="button" onClick={() => onAsk(citation)}>Ask about this passage</button></div>{contextError && <p className="chat-entry-error" role="alert">{contextError}</p>}{context && <div className="chat-surrounding-context">{context.previous && <section><strong>Previous passage</strong><p>{context.previous.text}</p></section>}<section className="is-current"><strong>Cited passage</strong><p>{context.current.text}</p></section>{context.next && <section><strong>Following passage</strong><p>{context.next.text}</p></section>}</div>}<p className="chat-evidence-note">{citation.evidenceStatus === 'retrieved_not_verified' ? 'Retrieved passage · not independently verified' : citation.evidenceStatus === 'acquired_full_text' ? 'Acquired full text' : 'Candidate source'}</p></div></Drawer>;
}

export function NoteEditor({ note, open, onClose, onSave, onDelete }: { note: WorkspaceNote | null; open: boolean; onClose: () => void; onSave: (title: string, body: string) => void; onDelete?: () => void }) {
  const [title, setTitle] = useState(''); const [body, setBody] = useState('');
  useEffect(() => { if (open) { setTitle(note?.title ?? ''); setBody(note?.body ?? ''); } }, [note, open]);
  if (!open) return null;
  return <Drawer title={note ? 'Edit note' : 'New note'} onClose={onClose}><div className="chat-note-editor"><label>Title<input value={title} onChange={event => setTitle(event.target.value)} placeholder="Note title" /></label><label>Note<textarea value={body} onChange={event => setBody(event.target.value)} rows={12} placeholder="Capture a useful finding…" /></label><div className="chat-drawer-actions">{onDelete && <button type="button" className="is-danger" onClick={onDelete}>Delete</button>}<button type="button" className="is-primary" disabled={!body.trim()} onClick={() => onSave(title, body)}>Save note</button></div></div></Drawer>;
}

export function ArtifactCreationDrawer({ kind, sourceCount, onClose, onGenerate }: { kind: CreateArtifactKind | null; sourceCount: number; onClose: () => void; onGenerate: (prompt: string) => void }) {
  const [focus, setFocus] = useState(''); const [detailed, setDetailed] = useState(false);
  if (!kind) return null; const option = CREATE_OPTIONS.find(item => item.id === kind)!;
  return <Drawer title={`Create ${option.label.toLowerCase()}`} onClose={onClose}><div className="chat-create-drawer"><div className="chat-create-summary"><span>{sourceCount}</span><div><strong>Selected sources</strong><small>The result will use the context currently active in Chat.</small></div></div><label>Focus<textarea rows={5} value={focus} onChange={event => setFocus(event.target.value)} placeholder="Focus on the most important risks, recurring themes and recommended actions." /></label><fieldset><legend>Detail</legend><div className="chat-detail-choice"><button type="button" className={!detailed ? 'is-selected' : ''} onClick={() => setDetailed(false)}>Concise</button><button type="button" className={detailed ? 'is-selected' : ''} onClick={() => setDetailed(true)}>Detailed</button></div></fieldset><label className="chat-check"><input type="checkbox" defaultChecked /> Include citations where supported</label><label className="chat-check"><input type="checkbox" defaultChecked /> Include recommendations</label><div className="chat-drawer-actions"><button type="button" className="is-primary" onClick={() => onGenerate(artifactPrompt(kind, focus, detailed))}>Generate {option.label.toLowerCase()}</button></div></div></Drawer>;
}

export function ActivityDrawer({ open, activities, runId, onClose }: { open: boolean; activities: AgentActivity[]; runId: string | null; onClose: () => void }) {
  if (!open) return null;
  return <Drawer title="Activity" onClose={onClose}><div className="chat-activity-list">{activities.length === 0 && <p className="chat-muted">Activity will appear when Studio starts working.</p>}{activities.map(activity => <div key={activity.nodeId} className={`chat-activity-row is-${activity.status}`}><span>{activity.status === 'completed' ? '✓' : activity.status === 'failed' ? '!' : activity.status === 'running' ? '●' : '○'}</span><div><strong>{activity.displayName}</strong><p>{activity.text}</p></div></div>)}{runId && <a className="chat-technical-link" href={`/cockpit/${encodeURIComponent(runId)}`}>Open technical execution</a>}</div></Drawer>;
}
