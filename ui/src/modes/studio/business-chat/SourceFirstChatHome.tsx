import { useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';

import { api } from '../../../api/client';
import { knowledgeApi, type CollectionResource, type DocumentResource, type RAGAgentDefinition } from '../../../api/knowledge';
import type { CloudFileRef, IntegrationConnectionInfo } from '../../../api/types';
import { ArtifactCreationDrawer, ChatHistoryDrawer, ExistingWorkflowDrawer, NoteEditor, SourcePickerDialog, type ExistingChatWorkflow } from './ChatWorkspaceOverlays';
import { NotebookSourcesPanel } from './NotebookSourcesPanel';
import { ChatWorkspaceShell, type ChatWorkspacePanel } from './ChatWorkspaceShell';
import { SessionAuditPanel, type SessionTab } from './SessionAuditPanel';
import { ComposerMenu, type ComposerMenuItem } from './ComposerMenu';
import {
  documentsAsSources,
  collectionAsSource,
  CREATE_OPTIONS,
  driveFilesAsSources,
  selectedCollectionId,
  selectedFiles,
  selectedKnowledgeDocumentIds,
  selectedSourceCount,
  uploadsAsSources,
  webSourcesFromText,
  type CreateArtifactKind,
  type WorkspaceNote,
  type WorkspaceSource,
} from './chatWorkspaceModel';
import {
  createNote,
  createLocalChat,
  deleteLocalChat,
  ensureLocalChat,
  loadLocalChatHistory,
  savePendingSources,
  loadNotes,
  loadWorkspacePreferences,
  saveNotes,
  saveWorkspacePreferences,
  saveWorkspaceSources,
  loadWorkspaceSources,
  updateLocalChat,
  type LocalChatRecord,
} from './chatWorkspaceStorage';
import './chatWorkspace.css';

const SUGGESTIONS = [
  'Summarize the key findings',
  'What are the biggest risks?',
  'Compare these sources',
  'Find contradictions',
  'Create an executive brief',
];

export function SourceFirstChatHome() {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [activeChat, setActiveChat] = useState(() => ensureLocalChat(searchParams.get('chat')));
  const workspaceId = `local:${activeChat.id}`;
  const [initialPreferences] = useState(() => loadWorkspacePreferences());
  const [composerText, setComposerText] = useState('');
  const [sources, setSources] = useState<WorkspaceSource[]>(() => loadWorkspaceSources(workspaceId));
  const [notes, setNotes] = useState<WorkspaceNote[]>(() => loadNotes(workspaceId));
  const [sourcesCollapsed, setSourcesCollapsed] = useState(initialPreferences.sourcesCollapsed);
  const [sessionsCollapsed, setSessionsCollapsed] = useState(initialPreferences.sessionsCollapsed);
  const [sourcesWidth, setSourcesWidth] = useState(initialPreferences.sourcesWidth);
  const [sessionWidth, setSessionWidth] = useState(initialPreferences.sessionWidth);
  const [distractionFree, setDistractionFree] = useState(initialPreferences.distractionFree);
  const [sessionTab, setSessionTab] = useState<SessionTab>('overview');
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
  const [noteEditorOpen, setNoteEditorOpen] = useState(false);
  const [activeNote, setActiveNote] = useState<WorkspaceNote | null>(null);
  const [createKind, setCreateKind] = useState<CreateArtifactKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mobilePanel, setMobilePanel] = useState<ChatWorkspacePanel>('chat');
  const [composerMenu, setComposerMenu] = useState<'skill' | 'create' | null>(null);
  const [skills, setSkills] = useState<ComposerMenuItem[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<ComposerMenuItem | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [workflowPickerOpen, setWorkflowPickerOpen] = useState(false);
  const [existingWorkflows, setExistingWorkflows] = useState<ExistingChatWorkflow[]>([]);
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [workflowError, setWorkflowError] = useState<string | null>(null);
  const [knowledgeChoices, setKnowledgeChoices] = useState<RAGAgentDefinition[]>([]);

  useEffect(() => {
    saveWorkspacePreferences({ sourcesCollapsed, sessionsCollapsed, sourcesWidth, sessionWidth, distractionFree });
  }, [distractionFree, sessionWidth, sessionsCollapsed, sourcesCollapsed, sourcesWidth]);

  useEffect(() => {
    let cancelled = false;
    api.researchSkills()
      .then(result => {
        if (!cancelled) setSkills(result.skills.map(skill => ({
          id: skill.name,
          label: skill.name.split('-').map(part => part.charAt(0).toUpperCase() + part.slice(1)).join(' '),
          description: skill.description,
        })));
      })
      .catch(() => { if (!cancelled) setSkills([]); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => saveNotes(workspaceId, notes), [notes, workspaceId]);
  useEffect(() => saveWorkspaceSources(workspaceId, sources), [sources, workspaceId]);
  useEffect(() => { if (searchParams.get('chat') !== activeChat.id) setSearchParams({ chat: activeChat.id }, { replace: true }); }, [activeChat.id, searchParams, setSearchParams]);

  function openChat(chat: LocalChatRecord) {
    setHistoryOpen(false);
    if (chat.workflowId && chat.workflowSource) {
      navigate(`/chat/${chat.workflowSource}/${encodeURIComponent(chat.workflowId)}?chat=${encodeURIComponent(chat.id)}`);
      return;
    }
    setActiveChat(ensureLocalChat(chat.id));
    setSources(loadWorkspaceSources(`local:${chat.id}`));
    setNotes(loadNotes(`local:${chat.id}`));
    setComposerText('');
  }

  function newChat() {
    const chat = createLocalChat();
    setHistoryRevision(value => value + 1);
    openChat(chat);
  }

  async function openWorkflowPicker() {
    setWorkflowPickerOpen(true);
    setWorkflowLoading(true);
    setWorkflowError(null);
    try {
      const [privateResult, sharedResult] = await Promise.all([
        api.listPrivateChatWorkflows(),
        api.listWorkflows(),
      ]);
      setExistingWorkflows([
        ...privateResult.workflows
          .filter(workflow => workflow.status !== 'archived')
          .map(workflow => ({ id: workflow.id, source: 'private' as const, title: workflow.name, description: workflow.description, status: workflow.status })),
        ...sharedResult.map(workflow => ({ id: workflow.name, source: 'shared' as const, title: workflow.library?.title ?? workflow.name, description: workflow.library?.summary ?? workflow.description, status: workflow.readiness.level })),
      ]);
    } catch (reason) {
      setWorkflowError(reason instanceof Error ? reason.message : 'Existing workflows could not be loaded.');
    } finally {
      setWorkflowLoading(false);
    }
  }

  function useExistingWorkflow(workflow: ExistingChatWorkflow) {
    updateLocalChat(activeChat.id, { title: workflow.title, workflowId: workflow.id, workflowSource: workflow.source, isGeneralChat: false });
    savePendingSources(workflow.id, sources);
    const files = selectedFiles(sources);
    if (files.length > 0) {
      window.sessionStorage.setItem(`eurskem.chat.pending-attachments:${workflow.id}`, JSON.stringify(files));
    }
    setWorkflowPickerOpen(false);
    navigate(`/chat/${workflow.source}/${encodeURIComponent(workflow.id)}?chat=${encodeURIComponent(activeChat.id)}`);
  }

  async function uploadFiles(files: File[]) {
    if (files.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const uploaded = await api.uploadWorkflowFiles(files);
      setSources(current => {
        const incoming = uploadsAsSources(uploaded.files);
        return [...current, ...incoming.filter(source => !current.some(item => item.id === source.id))];
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'The files could not be uploaded.');
    } finally {
      setBusy(false);
    }
  }

  async function importDriveFiles(connection: IntegrationConnectionInfo, picked: CloudFileRef[]) {
    if (picked.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const localFiles = await Promise.all(picked.map(async item => (
        new File([await api.downloadIntegrationFile(connection.id, item.id)], item.name, {
          type: item.mimeType || 'application/octet-stream',
          lastModified: item.modifiedAt ? new Date(item.modifiedAt).getTime() : Date.now(),
        })
      )));
      const uploaded = await api.uploadWorkflowFiles(localFiles);
      setSources(current => {
        const incoming = driveFilesAsSources(uploaded.files, picked, connection);
        return [...current, ...incoming.filter(source => !current.some(item => item.id === source.id))];
      });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : 'The selected Drive files could not be imported.';
      setError(message);
      throw reason;
    } finally {
      setBusy(false);
    }
  }

  function addWebSources(text: string) {
    const incoming = webSourcesFromText(text);
    if (incoming.length === 0) return;
    setSources(current => [...current, ...incoming.filter(item => !current.some(existing => existing.id === item.id))]);
  }

  function selectCollection(collection: CollectionResource, items: DocumentResource[]) {
    setSourceLoading(true);
    setSources(current => {
      const nonKnowledge = current.filter(source => source.kind !== 'collection' && source.kind !== 'document');
      const selectedIds = new Set(items.map(item => `document:${item.document_id}`));
      return [collectionAsSource(collection), ...nonKnowledge, ...documentsAsSources(items, collection.collection_id, selectedIds)];
    });
    setSourceLoading(false);
    setSourcePickerOpen(false);
    setMobilePanel('sources');
  }

  function removeSource(source: WorkspaceSource) {
    setSources(current => current.filter(item => (
      item.id !== source.id
      && !(source.kind === 'collection' && item.collectionId === source.collectionId)
    )));
  }

  async function startWorkspace(request = composerText) {
    const objective = request.trim();
    if (!objective || busy) return;
    setBusy(true);
    setError(null);
    try {
      const files = selectedFiles(sources);
      const collectionId = selectedCollectionId(sources);
      const documentIds = selectedKnowledgeDocumentIds(sources);
      if (collectionId) {
        if (documentIds.length === 0) {
          setError('Select at least one document from this Knowledge collection.');
          return;
        }
        const agents = (await knowledgeApi.listRagAgents()).filter(agent => (
          agent.collection_id === collectionId && ['active', 'ready'].includes(agent.status)
        ));
        if (agents.length === 0) {
          setKnowledgeChoices([]);
          setError('This collection does not have an active RAG Agent yet. Configure one in Knowledge Studio, then return to this saved draft.');
          return;
        }
        if (agents.length > 1) {
          setKnowledgeChoices(agents);
          setError(null);
          return;
        }
        const prepared = await api.prepareChatWorkspace({ objective, collection_id: collectionId, rag_agent_id: agents[0].rag_agent_id, document_ids: documentIds });
        const workflow = prepared.workflow;
        savePendingSources(workflow.id, sources);
        updateLocalChat(activeChat.id, {
          title: objective.slice(0, 60), workflowId: workflow.id, workflowSource: 'private', isGeneralChat: true,
          collectionId, ragAgentId: agents[0].rag_agent_id,
        });
        navigate(`/chat/private/${encodeURIComponent(workflow.id)}?chat=${encodeURIComponent(activeChat.id)}&prompt=${encodeURIComponent(objective)}`);
        return;
      }
      const attachmentCategories = [...new Set(files.map(file => file.category))];
      const planningRequest = {
        objective,
        ...(files.length > 0 ? {
          has_attachments: true as const,
          attachment_categories: attachmentCategories,
        } : {}),
      };
      const plan = selectedSkill ? null : await api.planChatWorkspace(planningRequest);
      const prepared = selectedSkill
        ? await api.prepareChatWorkspace({ ...planningRequest, skill_name: selectedSkill.id })
        : plan?.kind !== 'llm'
          ? await api.prepareChatWorkspace(planningRequest)
          : null;
      const workflow = prepared?.workflow ?? await api.ensureGeneralChatWorkflow();
      if (files.length > 0) {
        window.sessionStorage.setItem(
          `eurskem.chat.pending-attachments:${workflow.id}`,
          JSON.stringify(files),
        );
      }
      savePendingSources(workflow.id, sources);
      updateLocalChat(activeChat.id, {
        title: objective.slice(0, 60), workflowId: workflow.id, workflowSource: 'private',
        isGeneralChat: true,
      });
      navigate(`/chat/private/${encodeURIComponent(workflow.id)}?chat=${encodeURIComponent(activeChat.id)}&prompt=${encodeURIComponent(objective)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  async function chooseKnowledgeAgent(agent: RAGAgentDefinition) {
    const objective = composerText.trim();
    const collectionId = selectedCollectionId(sources);
    const documentIds = selectedKnowledgeDocumentIds(sources);
    if (!objective || !collectionId || busy) return;
    if (documentIds.length === 0) {
      setError('Select at least one document from this Knowledge collection.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const prepared = await api.prepareChatWorkspace({ objective, collection_id: collectionId, rag_agent_id: agent.rag_agent_id, document_ids: documentIds });
      const workflow = prepared.workflow;
      savePendingSources(workflow.id, sources);
      updateLocalChat(activeChat.id, {
        title: objective.slice(0, 60), workflowId: workflow.id, workflowSource: 'private', isGeneralChat: true,
        collectionId, ragAgentId: agent.rag_agent_id,
      });
      setKnowledgeChoices([]);
      navigate(`/chat/private/${encodeURIComponent(workflow.id)}?chat=${encodeURIComponent(activeChat.id)}&prompt=${encodeURIComponent(objective)}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  function configureKnowledge() {
    const collectionId = selectedCollectionId(sources);
    if (collectionId) window.localStorage.setItem('eurskem.knowledge.collection', collectionId);
    window.localStorage.setItem('eurskem.knowledge.tab', 'agents');
    window.dispatchEvent(new Event('eurskem:open-knowledge'));
  }

  function saveNote(title: string, body: string) {
    setNotes(current => activeNote
      ? current.map(note => note.id === activeNote.id
        ? { ...note, title: title.trim() || 'Untitled note', body: body.trim(), updatedAt: new Date().toISOString() }
        : note)
      : [...current, createNote(title, body)]);
    setNoteEditorOpen(false);
    setActiveNote(null);
  }

  const selectedCount = selectedSourceCount(sources);
  const hasKnowledgeSelection = sources.some(source => source.selected && (source.kind === 'collection' || source.kind === 'document'));
  const createItems: ComposerMenuItem[] = CREATE_OPTIONS.map(item => ({ ...item }));

  return (
    <div className="chat-workspace">
      <header className="chat-workspace-header">
        <div>
          <h1>{activeChat.title}</h1>
          <p>{selectedCount} source{selectedCount === 1 ? '' : 's'} selected</p>
        </div>
        <div className="chat-workspace-header-actions">
          <button type="button" onClick={newChat}>New chat</button>
          <button type="button" className="chat-history-trigger" onClick={() => setHistoryOpen(true)}>Chats</button>
          <button type="button" onClick={() => { setSessionsCollapsed(false); setMobilePanel('session'); }}>Session</button>
          <button type="button" aria-pressed={distractionFree} onClick={() => setDistractionFree(value => !value)}>{distractionFree ? 'Show panels' : 'Focus'}</button>
          <button type="button" aria-label="More Chat options">•••</button>
        </div>
      </header>

      <ChatWorkspaceShell
        sources={<NotebookSourcesPanel
            sources={sources}
            notes={notes}
            collapsed={sourcesCollapsed}
            loading={sourceLoading}
            onCollapse={() => setSourcesCollapsed(value => !value)}
            onToggle={sourceId => setSources(current => {
              const target = current.find(source => source.id === sourceId);
              if (!target) return current;
              const selected = !target.selected;
              return current.map(source => (
                source.id === sourceId || (target.kind === 'collection' && source.collectionId === target.collectionId)
                  ? { ...source, selected }
                  : source
              ));
            })}
            onToggleAll={selected => setSources(current => current.map(source => ({ ...source, selected: selected && ['ready', 'synced', 'outdated'].includes(source.status) })))}
            onAddSources={() => setSourcePickerOpen(true)}
            onOpenSource={source => { if (source.sourceUrl) window.open(source.sourceUrl, '_blank', 'noopener,noreferrer'); }}
            onShowUsage={() => { setSessionTab('sources'); setSessionsCollapsed(false); setMobilePanel('session'); }}
            onRemoveSource={removeSource}
            onFilesDropped={files => void uploadFiles(files)}
            onOpenNote={note => { setActiveNote(note); setNoteEditorOpen(true); }}
            onNewNote={() => { setActiveNote(null); setNoteEditorOpen(true); }}
          />}

        conversation={<main className="chat-conversation">
          <div className="chat-empty-state">
            <div className="chat-empty-mark" aria-hidden>✦</div>
            <h2>What would you like to understand?</h2>
            <p>Ask questions, compare sources, find patterns,<br />or create something from your knowledge.</p>
            <div className="chat-suggestions">
              {SUGGESTIONS.map(suggestion => (
                <button type="button" key={suggestion} onClick={() => setComposerText(suggestion)}>{suggestion}</button>
              ))}
            </div>
          </div>

          <form className="chat-entry-composer" onSubmit={event => { event.preventDefault(); void startWorkspace(); }}>
            {hasKnowledgeSelection && (
              <p className="chat-source-scope">Using {selectedKnowledgeDocumentIds(sources).length} selected Knowledge document{selectedKnowledgeDocumentIds(sources).length === 1 ? '' : 's'}.</p>
            )}
            {selectedCount === 0 && <p className="chat-source-warning">No sources selected. Chat can still answer general questions.</p>}
            <textarea
              value={composerText}
              onChange={event => setComposerText(event.target.value)}
              onPaste={event => {
                const images = [...event.clipboardData.items]
                  .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
                  .flatMap(item => item.getAsFile() ? [item.getAsFile() as File] : []);
                if (images.length > 0) {
                  event.preventDefault();
                  void uploadFiles(images);
                  return;
                }
                addWebSources(event.clipboardData.getData('text/plain'));
              }}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void startWorkspace();
                }
              }}
              onDragOver={event => event.preventDefault()}
              onDrop={event => { event.preventDefault(); void uploadFiles(Array.from(event.dataTransfer.files)); }}
              rows={3}
              placeholder="Ask anything about your sources…"
              aria-label="Chat message"
            />
            {composerMenu === 'skill' && (
              <ComposerMenu label="Choose a skill" items={skills} onClose={() => setComposerMenu(null)} onChoose={item => {
                setSelectedSkill(item);
                setComposerMenu(null);
              }} />
            )}
            {composerMenu === 'create' && (
              <ComposerMenu label="Create something" items={createItems} onClose={() => setComposerMenu(null)} onChoose={item => { setCreateKind(item.id as CreateArtifactKind); setComposerMenu(null); }} />
            )}
            <div className="chat-entry-toolbar">
              <div>
                <label className="chat-composer-action">
                  <input type="file" multiple className="sr-only" onChange={event => { void uploadFiles(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
                  + Attach
                </label>
                <button type="button" onClick={() => setSourcePickerOpen(true)}>Sources · {selectedCount}</button>
                <button type="button" onClick={() => void openWorkflowPicker()}>Workflows</button>
                <button type="button" aria-expanded={composerMenu === 'skill'} onClick={() => setComposerMenu(value => value === 'skill' ? null : 'skill')}>{selectedSkill ? `@ ${selectedSkill.label}` : '@ Skill'}</button>
                <button type="button" aria-expanded={composerMenu === 'create'} onClick={() => setComposerMenu(value => value === 'create' ? null : 'create')}>/ Create</button>
              </div>
              <button type="submit" className="chat-send-button" disabled={!composerText.trim() || busy} aria-label="Send message">
                {busy ? '…' : '↑'}
              </button>
            </div>
            {error && <div className="chat-entry-error" role="alert">{error}</div>}
            {knowledgeChoices.length > 1 && (
              <div className="chat-knowledge-choices" aria-label="Choose a Knowledge RAG Agent">
                <strong>Choose how to search this collection</strong>
                {knowledgeChoices.map(agent => (
                  <button type="button" key={agent.rag_agent_id} onClick={() => void chooseKnowledgeAgent(agent)}>
                    <span>{agent.name}</span><small>{agent.description || 'Grounded answers using this collection.'}</small>
                  </button>
                ))}
              </div>
            )}
            {error?.includes('does not have an active RAG Agent') && (
              <button type="button" className="chat-knowledge-configure" onClick={configureKnowledge}>Configure RAG Agent in Knowledge Studio</button>
            )}
          </form>
        </main>}
        session={<SessionAuditPanel title={activeChat.title} collapsed={sessionsCollapsed} run={null} audit={[]} activities={[]} sources={sources} messageCount={0} workflowLabel={selectedSkill?.label ?? 'General Chat'} activeTab={sessionTab} selectedNodeId={null} onCollapse={() => setSessionsCollapsed(value => !value)} onOpenHistory={() => setHistoryOpen(true)} onNewChat={newChat} onTabChange={setSessionTab} onSelectNode={() => undefined} onSelectSource={() => setMobilePanel('sources')} onOpenTechnical={() => undefined} />}
        sourcesCollapsed={sourcesCollapsed}
        sessionCollapsed={sessionsCollapsed}
        sourcesWidth={sourcesWidth}
        sessionWidth={sessionWidth}
        mobilePanel={mobilePanel}
        distractionFree={distractionFree}
        onSourcesWidthChange={setSourcesWidth}
        onSessionWidthChange={setSessionWidth}
        onMobilePanelChange={setMobilePanel}
      />

      <SourcePickerDialog
        open={sourcePickerOpen}
        sourceCount={sources.length}
        knowledgeEnabled
        onClose={() => setSourcePickerOpen(false)}
        onUpload={files => void uploadFiles(files)}
        onAddUrls={addWebSources}
        onSelectCollection={selectCollection}
        onImportDrive={importDriveFiles}
      />
      <ChatHistoryDrawer open={historyOpen} chats={loadLocalChatHistory().chats} activeChatId={activeChat.id} onClose={() => setHistoryOpen(false)} onNew={newChat} onOpen={openChat} onRename={chat => { const title = window.prompt('Rename chat', chat.title)?.trim(); if (title) { const updated = updateLocalChat(chat.id, { title }); if (updated?.id === activeChat.id) setActiveChat(updated); setHistoryRevision(value => value + 1); } }} onDelete={chat => { if (!window.confirm(`Delete “${chat.title}” from this browser?`)) return; const next = deleteLocalChat(chat.id) ?? createLocalChat(); setHistoryRevision(value => value + 1); if (chat.id === activeChat.id) openChat(next); }} key={historyRevision} />
      <ExistingWorkflowDrawer open={workflowPickerOpen} workflows={existingWorkflows} loading={workflowLoading} error={workflowError} onClose={() => setWorkflowPickerOpen(false)} onOpen={useExistingWorkflow} />
      <NoteEditor
        note={activeNote}
        open={noteEditorOpen}
        onClose={() => { setNoteEditorOpen(false); setActiveNote(null); }}
        onSave={saveNote}
        onDelete={activeNote ? () => {
          setNotes(current => current.filter(note => note.id !== activeNote.id));
          setNoteEditorOpen(false);
          setActiveNote(null);
        } : undefined}
      />
      <ArtifactCreationDrawer
        kind={createKind}
        sourceCount={selectedCount}
        onClose={() => setCreateKind(null)}
        onGenerate={prompt => { setComposerText(prompt); setCreateKind(null); setMobilePanel('chat'); }}
      />
    </div>
  );
}