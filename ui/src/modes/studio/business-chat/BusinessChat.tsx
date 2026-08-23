import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { api, apiBase, getAuthHeaders } from '../../../api/client';
import { knowledgeApi } from '../../../api/knowledge';
import type { AuditEvent, CloudFileRef, IntegrationConnectionInfo, RunDetail, RunEvent, WorkflowFileReference } from '../../../api/types';
import { CopyButton } from '../../../components/CopyButton';
import { RunControlBar } from './RunControlBar';
import { PromptTemplateLibrary } from './PromptTemplateLibrary';
import { SourceFirstChatHome } from './SourceFirstChatHome';
import { ArtifactCreationDrawer, ChatHistoryDrawer, CitationDrawer, NoteEditor, SourcePickerDialog } from './ChatWorkspaceOverlays';
import { NotebookSourcesPanel } from './NotebookSourcesPanel';
import { ChatWorkspaceShell, type ChatWorkspacePanel } from './ChatWorkspaceShell';
import { SessionAuditPanel, type SessionTab } from './SessionAuditPanel';
import { AgentActivityGroup } from './AgentActivityGroup';
import { ChatInterventionCard } from './ChatInterventionCard';
import { ComposerMenu, type ComposerMenuItem } from './ComposerMenu';
import {
  consumePendingSources,
  createLocalChat,
  createNote,
  deleteLocalChat,
  loadLocalChatHistory,
  loadNotes,
  loadWorkspaceSources,
  loadWorkspacePreferences,
  saveNotes,
  savePendingSources,
  saveWorkspacePreferences,
  saveWorkspaceSources,
  updateLocalChat,
  type LocalChatRecord,
} from './chatWorkspaceStorage';
import {
  CREATE_OPTIONS,
  driveFilesAsSources,
  friendlyError,
  selectedSourceCount,
  selectedFiles,
  uploadsAsSources,
  webSourcesFromText,
  type CitationTarget,
  type CreateArtifactKind,
  type WorkspaceNote,
  type WorkspaceSource,
} from './chatWorkspaceModel';
import {
  applySlashCommand, matchingSlashCommands,
  type SlashCommand,
} from './chatEnhancements';
import { observeChatRun } from './observeChatRun';
import type { RunControlAction } from './runControls';
import {
  collectRecognitionText,
  recognitionConstructor,
  speakText,
  speechRecognitionSupported,
  speechSynthesisSupported,
  stopSpeaking,
  type BrowserSpeechRecognition,
} from './speech';
import {
  assistantSegments,
  activityFromNodeRun,
  businessActivityLabel,
  buildRunInputs,
  chatMetaFromYaml,
  composerDisabledReason,
  interventionFromPendingGate,
  structuredResultFromRun,
  type AssistantSegment,
  type AgentActivity,
  type WorkflowChatMeta,
} from './businessChatModel';
import type { ChatArtifact } from './chatOutputs';
import {
  boundedConversationSummary,
  deserializeDurableMessage,
  serializeDurableMessage,
  type DurableChatMessage,
} from './chatTranscript';

/**
 * Business Chat: a published workflow experienced as a conversation.
 *
 * The first message runs the real workflow through the existing execution
 * API and SSE stream; a Human Intervention node switches the conversation
 * into an inline Chat review card (backed by the durable
 * pending-gate record, so a refresh restores it); follow-up messages ask
 * the existing Ask AI service about the run. Nothing here bypasses the
 * workflow runtime or re-implements retrieval, execution, or review.
 */

type ChatMessage = DurableChatMessage;

function newId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}
function activityKey(runId: string, nodeId: string): string {
  return `${runId}:${nodeId}`;
}
export function BusinessChat() {
  const { workflowName, chatWorkflowId } = useParams();
  if (!workflowName && !chatWorkflowId) return <SourceFirstChatHome />;
  if (chatWorkflowId) {
    return (
      <BusinessChatConversation
        key={`private:${chatWorkflowId}`}
        workflowId={decodeURIComponent(chatWorkflowId)}
        source="private"
      />
    );
  }
  return (
    <BusinessChatConversation
      key={`shared:${workflowName}`}
      workflowId={decodeURIComponent(workflowName as string)}
      source="shared"
    />
  );
}

// ---- Conversation ------------------------------------------------------

function BusinessChatConversation({ workflowId, source }: { workflowId: string; source: 'shared' | 'private' }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [localChat, setLocalChat] = useState(() => {
    const history = loadLocalChatHistory();
    const matching = history.chats.find(chat => chat.id === searchParams.get('chat'))
      ?? history.chats.find(chat => chat.workflowId === workflowId && chat.workflowSource === source);
    return matching ?? createLocalChat();
  });
  const [yamlText, setYamlText] = useState<string | null>(null);
  const [resourceName, setResourceName] = useState(workflowId);
  const [meta, setMeta] = useState<WorkflowChatMeta | null>(null);
  const [workflowIsGeneralPreset, setWorkflowIsGeneralPreset] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [transcriptSyncError, setTranscriptSyncError] = useState<string | null>(null);
  const [composerText, setComposerText] = useState('');
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [hasCompletedRun, setHasCompletedRun] = useState(false);
  const [activities, setActivities] = useState<Record<string, AgentActivity>>({});
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [sessionTab, setSessionTab] = useState<SessionTab>('overview');
  const [selectedActivityNodeId, setSelectedActivityNodeId] = useState<string | null>(null);
  const [pausePending, setPausePending] = useState(false);
  const [controlBusy, setControlBusy] = useState<RunControlAction | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [citation, setCitation] = useState<CitationTarget | null>(null);
  const [highlightedSourceId, setHighlightedSourceId] = useState<string | null>(null);
  const [noteEditorOpen, setNoteEditorOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState<WorkspaceNote | null>(null);
  const workspaceStorageId = `local:${localChat.id}`;
  const [notes, setNotes] = useState<WorkspaceNote[]>(() => loadNotes(workspaceStorageId));
  const [workspaceSources, setWorkspaceSources] = useState<WorkspaceSource[]>(() => loadWorkspaceSources(workspaceStorageId));
  const [initialPreferences] = useState(() => loadWorkspacePreferences());
  const [sourcesCollapsed, setSourcesCollapsed] = useState(initialPreferences.sourcesCollapsed);
  const [sessionsCollapsed, setSessionsCollapsed] = useState(initialPreferences.sessionsCollapsed);
  const [sourcesWidth, setSourcesWidth] = useState(initialPreferences.sourcesWidth);
  const [sessionWidth, setSessionWidth] = useState(initialPreferences.sessionWidth);
  const [distractionFree, setDistractionFree] = useState(initialPreferences.distractionFree);
  const [mobilePanel, setMobilePanel] = useState<ChatWorkspacePanel>('chat');
  const [createKind, setCreateKind] = useState<CreateArtifactKind | null>(null);
  const [composerMenu, setComposerMenu] = useState<'skill' | 'create' | null>(null);
  const [skills, setSkills] = useState<ComposerMenuItem[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<ComposerMenuItem | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [sourcePickerOpen, setSourcePickerOpen] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [attachments, setAttachments] = useState<WorkflowFileReference[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const [knowledgeScope, setKnowledgeScope] = useState<{ collection: string; agent: string } | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runSubmissionRef = useRef(false);
  const finalizedRunsRef = useRef(new Set<string>());
  const responseLabelsRef = useRef(new Map<string, string>());
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  function openLocalChat(chat: LocalChatRecord) {
    setHistoryOpen(false);
    if (chat.workflowId && chat.workflowSource) {
      navigate(`/chat/${chat.workflowSource}/${encodeURIComponent(chat.workflowId)}?chat=${encodeURIComponent(chat.id)}`);
    } else {
      navigate(`/chat?chat=${encodeURIComponent(chat.id)}`);
    }
  }

  function startLocalChat() {
    openLocalChat(createLocalChat());
  }

  useEffect(() => {
    const updated = updateLocalChat(localChat.id, { workflowId, workflowSource: source });
    if (updated) setLocalChat(updated);
    if (searchParams.get('chat') !== localChat.id) {
      const next = new URLSearchParams(searchParams);
      next.set('chat', localChat.id);
      setSearchParams(next, { replace: true });
    }
  // The route identity is intentionally adopted once per workflow transition.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflowId, source, localChat.id]);

  useEffect(() => {
    const prompt = searchParams.get('prompt');
    if (!prompt) return;
    setComposerText(prompt);
    const next = new URLSearchParams(searchParams);
    next.delete('prompt');
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

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

  useEffect(() => {
    const key = `eurskem.chat.pending-attachments:${workflowId}`;
    const pending = window.sessionStorage.getItem(key);
    if (!pending) return;
    try {
      const parsed = JSON.parse(pending) as WorkflowFileReference[];
      if (Array.isArray(parsed)) setAttachments(parsed);
    } catch {
      // Ignore malformed ephemeral handoff state; the uploaded files remain in storage.
    } finally {
      window.sessionStorage.removeItem(key);
    }
  }, [workflowId]);

  useEffect(() => {
    const handedOff = consumePendingSources(workflowId);
    if (handedOff.length > 0) setWorkspaceSources(handedOff);
  }, [workflowId]);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setLoadError(null);
    const workflowLoad = source === 'private'
      ? api.getPrivateChatWorkflow(workflowId, controller.signal)
          .then(item => ({ yaml: item.yaml, name: item.name, isGeneralChat: item.slug === 'general-chat' }))
      : api.getBuilderChatExecutionAdapter(workflowId, controller.signal)
          .then(item => ({ yaml: item.yaml, name: item.workflow_name, isGeneralChat: false }));
    Promise.all([
      workflowLoad,
      api.resolveBusinessChatConversation(source, workflowId),
    ])
      .then(([{ yaml, name, isGeneralChat }, transcript]) => {
        if (cancelled) return;
        const loadedMeta = chatMetaFromYaml(yaml);
        const restored = transcript.messages
          .map(deserializeDurableMessage)
          .filter((message): message is DurableChatMessage => message !== null)
          // Older clients briefly exposed SubprocessAgent's internal wait as
          // a human approval card. It was never actionable; remove those
          // persisted false interventions while retaining real HITL reviews.
          .filter(message => (
            message.role !== 'intervention'
            || loadedMeta.nodes.find(node => node.id === message.request.nodeId)?.type !== 'SubprocessAgent'
          ));
        setYamlText(yaml);
        setResourceName(name);
        setMeta(loadedMeta);
        setWorkflowIsGeneralPreset(isGeneralChat);
        setLocalChat(current => {
          const patch = {
            ...(current.title === 'New chat' ? { title: name } : {}),
            ...(isGeneralChat && !current.isGeneralChat ? { isGeneralChat: true } : {}),
          };
          if (Object.keys(patch).length === 0) return current;
          return updateLocalChat(current.id, patch) ?? current;
        });
        setConversationId(transcript.conversation.id);
        updateLocalChat(localChat.id, { conversationId: transcript.conversation.id });
        setMessages(restored);
        const latestRunMessage = [...restored].reverse().find(message => (
          'runId' in message && message.runId
        ) || message.role === 'intervention');
        const latestRunId = latestRunMessage?.role === 'intervention'
          ? latestRunMessage.request.parentRunId ?? latestRunMessage.request.runId
          : latestRunMessage && 'runId' in latestRunMessage ? latestRunMessage.runId : null;
        setCurrentRunId(latestRunId ?? null);
        setHasCompletedRun(restored.some(message => message.role === 'assistant' && Boolean(message.runId)));
      })
      .catch(err => {
        if (cancelled) return;
        setLoadError(
          err instanceof DOMException && err.name === 'AbortError'
            ? 'The workflow request timed out. Check that the backend is running, then try again.'
            : (err instanceof Error ? err.message : String(err)),
        );
      });
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [workflowId, source, loadAttempt, localChat.id]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  useEffect(() => () => {
    abortRef.current?.abort();
    recognitionRef.current?.abort();
    stopSpeaking();
  }, []);

  useEffect(() => {
    saveNotes(workspaceStorageId, notes);
  }, [notes, workspaceStorageId]);

  useEffect(() => {
    saveWorkspacePreferences({ sourcesCollapsed, sessionsCollapsed, sourcesWidth, sessionWidth, distractionFree });
  }, [distractionFree, sessionWidth, sessionsCollapsed, sourcesCollapsed, sourcesWidth]);

  useEffect(() => {
    saveWorkspaceSources(workspaceStorageId, workspaceSources);
  }, [workspaceSources, workspaceStorageId]);

  useEffect(() => {
    let cancelled = false;
    if (!localChat.collectionId || !localChat.ragAgentId) {
      setKnowledgeScope(null);
      return;
    }
    Promise.all([
      knowledgeApi.getCollection(localChat.collectionId),
      knowledgeApi.getRagAgent(localChat.ragAgentId),
    ]).then(([collection, agent]) => {
      if (!cancelled) setKnowledgeScope({ collection: collection.name, agent: agent.name });
    }).catch(() => {
      if (!cancelled) setKnowledgeScope({ collection: 'Selected collection', agent: 'Knowledge search' });
    });
    return () => { cancelled = true; };
  }, [localChat.collectionId, localChat.ragAgentId]);

  const patchMessage = useCallback((id: string, patch: (message: ChatMessage) => ChatMessage) => {
    setMessages(current => current.map(item => (item.id === id ? patch(item) : item)));
  }, []);

  const persistMessage = useCallback(async (message: DurableChatMessage) => {
    if (!conversationId) throw new Error('The durable conversation is not ready.');
    const serialized = serializeDurableMessage(message);
    await api.appendBusinessChatMessage(conversationId, {
      message_id: message.id,
      ...serialized,
    });
    setTranscriptSyncError(null);
  }, [conversationId]);

  const appendDurableMessage = useCallback((message: DurableChatMessage) => {
    setMessages(current => current.some(item => item.id === message.id)
      ? current
      : [...current, message]);
    void persistMessage(message).catch(error => {
      setTranscriptSyncError(error instanceof Error ? error.message : 'The transcript could not be saved.');
    });
  }, [persistMessage]);

  const hydrateRun = useCallback(async (runId: string) => {
    const { run, audit } = await api.runDetail(runId);
    setRunDetail(run);
    setAuditEvents(audit);
    if (meta) {
      setActivities(current => {
        const next = { ...current };
        for (const node of meta.nodes) {
          if (run.node_runs[node.id]) {
            next[activityKey(run.run_id, node.id)] = activityFromNodeRun(node, run.node_runs[node.id], meta);
          }
        }
        return next;
      });
    }
    return run;
  }, [meta]);

  const appendAssistantFromRun = useCallback(async (runId: string) => {
    if (finalizedRunsRef.current.has(runId)) return;
    finalizedRunsRef.current.add(runId);
    try {
      const run = await hydrateRun(runId);
      if (run.status === 'failed' || run.status === 'rejected') {
        appendDurableMessage({
          id: `run-result-${runId}`,
          role: 'error',
          text: run.error
            ? `I couldn't complete this request: ${run.error}`
            : run.status === 'rejected'
              ? 'This attempt was rejected before producing a final result.'
              : 'I couldn\'t complete this request. Open the activity view for details.',
          runId,
        });
        return;
      }
      const responseLabel = responseLabelsRef.current.get(runId);
      responseLabelsRef.current.delete(runId);
      appendDurableMessage({
        id: `run-result-${runId}`,
        role: 'assistant',
        segments: assistantSegments(run),
        runId,
        ...(structuredResultFromRun(run) !== null ? { structuredResult: structuredResultFromRun(run) } : {}),
        ...(responseLabel ? { responseLabel } : {}),
      });
      setHasCompletedRun(true);
      setCurrentRunId(runId);
      updateLocalChat(localChat.id, { runId });
    } catch {
      finalizedRunsRef.current.delete(runId);
      appendDurableMessage({
        id: `run-load-error-${runId}`,
        role: 'error',
        text: 'The workflow finished, but I could not load its result.',
        runId,
      });
    }
  }, [appendDurableMessage, hydrateRun, localChat.id]);

  const showGateOrResult = useCallback(async (runId: string) => {
    try {
      const gate = await api.pendingGate(runId);
      const intervention = meta ? interventionFromPendingGate(gate, meta) : null;
      if (intervention) {
        appendDurableMessage({
          id: `intervention-${intervention.gateId}`,
          role: 'intervention',
          request: intervention,
          status: 'pending',
        });
      }
    } catch {
      // The durable run detail still drives controls if the gate fetch fails.
    }
  }, [appendDurableMessage, meta]);

  const observeAttempt = useCallback((runId: string) => {
    if (!meta) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setRunning(true);
    void observeChatRun(runId, {
      signal: controller.signal,
      onEvent: (event: RunEvent) => {
        if (event.type === 'node_paused') {
          setRunning(false);
          const node = meta.nodes.find(item => item.id === event.node_id);
          void hydrateRun(runId).then(run => {
            setPausePending(false);
            if (node) {
              const waitingForSubprocess = run.pause_kind === 'subprocess';
              setActivities(current => ({
                ...current,
                [activityKey(runId, node.id)]: {
                  nodeId: node.id,
                  nodeType: node.type,
                  displayName: businessActivityLabel(node) ?? '',
                  agentRole: node.agentRole,
                  status: waitingForSubprocess ? 'running' : 'needs_input',
                  text: run.pause_kind === 'user_requested'
                    ? 'Paused before this step. Resume when you are ready.'
                    : waitingForSubprocess
                      ? 'Waiting for the selected workflow to finish…'
                      : 'I need your input before the workflow can continue.',
                  recoveryActions: node.recoveryActions,
                },
              }));
            }
            if (run.pause_kind === 'hitl_gate') void showGateOrResult(runId);
          }).catch(() => undefined);
          return;
        }
        if (event.type === 'run_completed' || event.type === 'run_failed' || event.type === 'run_rejected') {
          setPausePending(false);
          setRunning(false);
          void appendAssistantFromRun(runId);
          return;
        }
        if (!('node_id' in event) || !event.node_id) return;
        const node = meta.nodes.find(item => item.id === event.node_id);
        if (!node) return;
        if (event.type === 'node_started') {
          setActivities(current => ({
            ...current,
            [activityKey(runId, node.id)]: {
              nodeId: node.id,
              nodeType: node.type,
              displayName: businessActivityLabel(node) ?? '',
              agentRole: node.agentRole,
              status: 'running',
              text: meta.runningMessages[node.id] ?? node.purpose ?? `Working on ${node.displayName}…`,
              recoveryActions: node.recoveryActions,
            },
          }));
        } else if (event.type === 'node_completed' || event.type === 'node_reused') {
          void hydrateRun(runId).catch(() => undefined);
        }
      },
      onDisconnected: error => setControlError(`Live updates disconnected; reconnecting… ${error.message}`),
      onOpen: () => {
        setControlError(current => current?.startsWith('Live updates disconnected') ? null : current);
        void hydrateRun(runId).catch(() => undefined);
      },
    }).finally(() => {
      if (abortRef.current === controller) abortRef.current = null;
    });
  }, [appendAssistantFromRun, hydrateRun, meta, showGateOrResult]);


  const runWorkflowOnce = useCallback(async (
    text: string,
    displayText = text,
    execution?: { yaml: string; meta: WorkflowChatMeta; workflowId: string; responseLabel?: string },
  ) => {
    const executionYaml = execution?.yaml ?? yamlText;
    const executionMeta = execution?.meta ?? meta;
    const executionWorkflowId = execution?.workflowId ?? workflowId;
    if (!executionYaml || !executionMeta || !conversationId || runSubmissionRef.current) return;
    runSubmissionRef.current = true;
    setRunDetail(null);
    setAuditEvents([]);
    const messageId = newId();
    const requestedRunId = crypto.randomUUID();
    const userMessage: DurableChatMessage = {
      id: messageId, role: 'user', text: displayText.trim() !== '' ? displayText : '(submitted form)',
      runId: requestedRunId,
      ...(attachments.length > 0 ? { attachments } : {}),
    };
    if (localChat.isGeneralChat && ['New chat', 'General Chat'].includes(localChat.title)) {
      const updated = updateLocalChat(localChat.id, { title: userMessage.text.slice(0, 60) });
      if (updated) setLocalChat(updated);
    }
    setMessages(current => [...current, userMessage]);
    setRunning(true);
    try {
      await persistMessage(userMessage);
      const conversationSummary = boundedConversationSummary(messages);
      const webSourceUrls = workspaceSources.flatMap(source => (
        source.selected && source.kind === 'web' && source.sourceUrl ? [source.sourceUrl] : []
      ));
      const inputs = buildRunInputs(executionMeta, text, {}, attachments, {
        ...(conversationSummary ? { conversation_summary: conversationSummary } : {}),
        ...(webSourceUrls.length > 0 ? { web_source_urls: webSourceUrls } : {}),
      });
      const { run_id: runId } = await api.runWorkflow(executionYaml, inputs, {
        origin: 'chat_saved_workflow',
        history_visibility: localChat.isGeneralChat ? 'conversation_only' : 'global',
        run_id: requestedRunId,
        workflow_id: executionWorkflowId,
        conversation_id: conversationId,
        message_id: messageId,
      });
      if (execution?.responseLabel) responseLabelsRef.current.set(runId, execution.responseLabel);
      setCurrentRunId(runId);
      updateLocalChat(localChat.id, { runId });
      setAttachments([]);
      observeAttempt(runId);
      await hydrateRun(runId).catch(() => undefined);
    } catch (err) {
      setRunning(false);
      appendDurableMessage({
        id: newId(),
        role: 'error',
        text: err instanceof Error ? err.message : 'The workflow could not be started.',
        runId: null,
      });
      runSubmissionRef.current = false;
    } finally {
      // The next message is classified automatically from conversation state.
    }
  }, [appendDurableMessage, attachments, conversationId, hydrateRun, localChat.id, localChat.isGeneralChat, localChat.title, messages, meta, observeAttempt, persistMessage, workflowId, workspaceSources, yamlText]);

  useEffect(() => {
    if (!running) runSubmissionRef.current = false;
  }, [running]);

  const uploadAttachments = useCallback(async (files: File[]) => {
    if (!meta?.allowAttachments || files.length === 0) return;
    setUploading(true);
    setAttachmentError(null);
    try {
      const uploaded = await api.uploadWorkflowFiles(files);
      setAttachments(current => [
        ...current,
        ...uploaded.files.filter(file => !current.some(existing => existing.file_id === file.file_id)),
      ]);
      setWorkspaceSources(current => {
        const incoming = uploadsAsSources(uploaded.files);
        return [...current, ...incoming.filter(item => !current.some(existing => existing.id === item.id))];
      });
    } catch (error) {
      setAttachmentError(error instanceof Error ? error.message : 'The files could not be uploaded.');
    } finally {
      setUploading(false);
    }
  }, [meta?.allowAttachments]);

  const importDriveFiles = useCallback(async (connection: IntegrationConnectionInfo, picked: CloudFileRef[]) => {
    if (!meta?.allowAttachments) throw new Error('This workflow does not accept file sources. You can still add website URLs.');
    if (picked.length === 0) return;
    setUploading(true);
    setAttachmentError(null);
    try {
      const localFiles = await Promise.all(picked.map(async item => (
        new File([await api.downloadIntegrationFile(connection.id, item.id)], item.name, {
          type: item.mimeType || 'application/octet-stream',
          lastModified: item.modifiedAt ? new Date(item.modifiedAt).getTime() : Date.now(),
        })
      )));
      const uploaded = await api.uploadWorkflowFiles(localFiles);
      setAttachments(current => [...current, ...uploaded.files.filter(file => !current.some(existing => existing.file_id === file.file_id))]);
      setWorkspaceSources(current => {
        const incoming = driveFilesAsSources(uploaded.files, picked, connection);
        return [...current, ...incoming.filter(item => !current.some(existing => existing.id === item.id))];
      });
    } catch (error) {
      setAttachmentError(error instanceof Error ? error.message : 'The selected Drive files could not be imported.');
      throw error;
    } finally {
      setUploading(false);
    }
  }, [meta?.allowAttachments]);

  const addWebSources = useCallback((text: string) => {
    const incoming = webSourcesFromText(text);
    if (incoming.length === 0) return;
    setWorkspaceSources(current => [...current, ...incoming.filter(item => !current.some(existing => existing.id === item.id))]);
  }, []);

  const toggleDictation = useCallback(() => {
    if (listening) {
      recognitionRef.current?.stop();
      return;
    }
    const Constructor = recognitionConstructor();
    if (!Constructor) {
      setSpeechError('Dictation is not supported by this browser. You can still type your answer.');
      return;
    }
    setSpeechError(null);
    const recognition = new Constructor();
    recognition.continuous = true;
    recognition.interimResults = false;
    recognition.lang = navigator.language || 'en-US';
    recognition.onresult = event => {
      const result = collectRecognitionText(event);
      if (!result.transcript) return;
      setComposerText(current => `${current}${current.trim() ? ' ' : ''}${result.transcript}`);
    };
    recognition.onerror = event => {
      setSpeechError(event.error === 'not-allowed'
        ? 'Microphone permission was denied. Allow microphone access to use dictation.'
        : `Dictation stopped${event.error ? `: ${event.error}` : '.'}`);
      setListening(false);
    };
    recognition.onend = () => setListening(false);
    recognitionRef.current = recognition;
    recognition.start();
    setListening(true);
  }, [listening]);

  const askFollowUp = useCallback(async (question: string, displayQuestion = question) => {
    if (!currentRunId) return;
    const userMessage: DurableChatMessage = { id: newId(), role: 'user', text: displayQuestion };
    setMessages(current => [...current, userMessage]);
    setAskBusy(true);
    try {
      await persistMessage(userMessage);
    } catch (error) {
      setTranscriptSyncError(error instanceof Error ? error.message : 'The transcript could not be saved.');
      setMessages(current => [...current, {
        id: newId(),
        role: 'error',
        text: 'I could not save that follow-up, so it was not sent. Please try again.',
        runId: currentRunId,
      }]);
      setAskBusy(false);
      return;
    }
    try {
      const history = await api.runChatHistory(currentRunId);
      const { answer } = await api.askAboutRun(currentRunId, question, history.turns);
      const assistantMessage: DurableChatMessage = {
        id: newId(),
        role: 'assistant',
        segments: [{ kind: 'text', text: answer }],
        runId: currentRunId,
      };
      setMessages(current => [...current, assistantMessage]);
      await persistMessage(assistantMessage);
    } catch {
      const errorMessage: DurableChatMessage = {
        id: newId(),
        role: 'error',
        text: 'I couldn\'t answer that follow-up right now. The workflow result above is unchanged.',
        runId: currentRunId,
      };
      setMessages(current => [...current, errorMessage]);
      void persistMessage(errorMessage).catch(syncError => setTranscriptSyncError(
        syncError instanceof Error ? syncError.message : 'The transcript could not be saved.',
      ));
    } finally {
      setAskBusy(false);
    }
  }, [currentRunId, persistMessage]);

  const pauseCurrentRun = useCallback(async () => {
    if (!currentRunId || runDetail?.status !== 'running' || pausePending) return;
    setControlBusy('pause');
    setControlError(null);
    try {
      await api.pauseRun(currentRunId);
      setPausePending(true);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : String(reason));
      void hydrateRun(currentRunId).catch(() => undefined);
    } finally {
      setControlBusy(null);
    }
  }, [currentRunId, hydrateRun, pausePending, runDetail?.status]);

  const resumeCurrentRun = useCallback(async () => {
    if (!currentRunId || runDetail?.status !== 'paused' || runDetail.pause_kind !== 'user_requested') return;
    setControlBusy('resume');
    setControlError(null);
    setRunning(true);
    observeAttempt(currentRunId);
    try {
      await api.resumePausedRun(currentRunId);
      await hydrateRun(currentRunId);
    } catch (reason) {
      setRunning(false);
      setControlError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setControlBusy(null);
    }
  }, [currentRunId, hydrateRun, observeAttempt, runDetail]);

  const startNewAttempt = useCallback(async (mode: 'retry' | 'restart') => {
    if (!currentRunId || !runDetail) return;
    const sourceRunId = currentRunId;
    const newRunId = crypto.randomUUID();
    const nextAttempt = (runDetail.attempt ?? 1) + 1;
    setControlBusy(mode);
    setControlError(null);
    setPausePending(false);
    setCurrentRunId(newRunId);
    setRunDetail(null);
    const attemptMessage: DurableChatMessage = {
      id: `attempt-${newRunId}`,
      role: 'attempt',
      runId: newRunId,
      text: mode === 'retry'
        ? `Attempt ${nextAttempt} · retrying with completed steps`
        : `Attempt ${nextAttempt} · restarting from the beginning`,
    };
    setMessages(current => [...current, attemptMessage]);
    observeAttempt(newRunId);
    try {
      if (mode === 'retry') await api.retryFailedRun(sourceRunId, newRunId);
      else await api.restartRun(sourceRunId, newRunId);
      void persistMessage(attemptMessage).catch(error => setTranscriptSyncError(
        error instanceof Error ? error.message : 'The transcript could not be saved.',
      ));
      await hydrateRun(newRunId);
    } catch (reason) {
      abortRef.current?.abort();
      setRunning(false);
      setCurrentRunId(sourceRunId);
      await hydrateRun(sourceRunId).catch(() => undefined);
      setControlError(reason instanceof Error ? reason.message : String(reason));
      setMessages(current => current.filter(message => message.id !== `attempt-${newRunId}`));
    } finally {
      setControlBusy(null);
    }
  }, [currentRunId, hydrateRun, observeAttempt, persistMessage, runDetail]);

  const onInterventionResult = useCallback(async (result: unknown, messageId: string, decision?: string) => {
    const record = (result ?? {}) as Record<string, unknown>;
    const status = typeof record.status === 'string' ? record.status : 'completed';
    const interventionMessage = messages.find(message => message.id === messageId);
    const nestedChildGate = interventionMessage?.role === 'intervention'
      && Boolean(interventionMessage.request.parentRunId);
    const resolvedMessage = interventionMessage?.role === 'intervention'
      ? { ...interventionMessage, status: 'resolved' as const, resolution: decision ?? status }
      : null;
    if (resolvedMessage) {
      patchMessage(messageId, () => resolvedMessage);
      if (conversationId) {
        const serialized = serializeDurableMessage(resolvedMessage);
        void api.replaceBusinessChatMessage(conversationId, messageId, serialized)
          .then(() => setTranscriptSyncError(null))
          .catch(error => setTranscriptSyncError(
            error instanceof Error ? error.message : 'The transcript could not be updated.',
          ));
      }
    }
    const runId = currentRunId;
    if (!runId) return;
    observeAttempt(runId);
    if (nestedChildGate) {
      // The decision resumed a child workflow. Its terminal result is delivered
      // to the parent through the subprocess callback; keep observing the
      // parent instead of mistaking the child's response for the Chat result.
      await hydrateRun(runId).catch(() => undefined);
      if (status === 'paused') await showGateOrResult(runId);
      return;
    }
    if (status === 'paused') {
      // The resumed run paused at the next gate — surface it.
      await hydrateRun(runId);
      await showGateOrResult(runId);
      return;
    }
    if (status === 'completed' || status === 'failed' || status === 'rejected') {
      setRunning(false);
      await appendAssistantFromRun(runId);
    }
  }, [appendAssistantFromRun, conversationId, currentRunId, hydrateRun, messages, observeAttempt, patchMessage, showGateOrResult]);

  useEffect(() => {
    if (!meta || !currentRunId) return;
    let cancelled = false;
    void hydrateRun(currentRunId).then(run => {
      if (cancelled) return;
      if (run.status === 'running') observeAttempt(currentRunId);
      else if (run.status === 'paused') void showGateOrResult(currentRunId);
      else if (run.status === 'completed' || run.status === 'failed' || run.status === 'rejected') {
        void appendAssistantFromRun(currentRunId);
      }
    }).catch(() => undefined);
    return () => { cancelled = true; };
  }, [appendAssistantFromRun, currentRunId, hydrateRun, meta, observeAttempt, showGateOrResult]);

  const interventionPending = messages.some(
    message => message.role === 'intervention'
      && message.status === 'pending'
      && meta?.nodes.find(node => node.id === message.request.nodeId)?.type !== 'SubprocessAgent',
  );
  const busy = running || askBusy || uploading;
  const disabledReason = runDetail?.status === 'paused'
    ? runDetail.pause_kind === 'user_requested'
      ? 'Resume this paused attempt before sending another message.'
      : runDetail.pause_kind === 'subprocess'
        ? 'Waiting for the selected workflow to finish…'
        : 'Resolve the pending review to continue.'
    : composerDisabledReason(interventionPending, busy);

  const contextActivities = useMemo(() => (
    currentRunId && meta
      ? Object.fromEntries(meta.nodes.flatMap(node => {
          const activity = activities[activityKey(currentRunId, node.id)];
          return activity ? [[node.id, activity]] : [];
        }))
      : {}
  ), [activities, currentRunId, meta]);
  const activeSourceCount = selectedSourceCount(workspaceSources);
  const activeSources = workspaceSources.filter(source => source.selected);
  const openDeepResearch = useCallback(async (question = '') => {
    setControlError(null);
    try {
      const workflow = await api.ensureDeepResearchChatWorkflow(localChat.ragAgentId);
      const selectedSources = workspaceSources.filter(source => source.selected);
      const files = selectedFiles(selectedSources);
      if (files.length > 0) {
        window.sessionStorage.setItem(
          `eurskem.chat.pending-attachments:${workflow.id}`,
          JSON.stringify(files),
        );
      }
      savePendingSources(workflow.id, selectedSources);
      const deepResearchChat = createLocalChat(question.trim().slice(0, 60) || 'Deep Research');
      updateLocalChat(deepResearchChat.id, {
        workflowId: workflow.id,
        workflowSource: 'private',
        isGeneralChat: true,
        collectionId: localChat.collectionId ?? null,
        ragAgentId: localChat.ragAgentId ?? null,
      });
      const params = new URLSearchParams({ chat: deepResearchChat.id });
      if (question.trim()) params.set('prompt', question.trim());
      navigate(`/chat/private/${encodeURIComponent(workflow.id)}?${params.toString()}`);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : 'Deep Research is unavailable.');
    }
  }, [localChat.collectionId, localChat.ragAgentId, navigate, workspaceSources]);

  const submit = useCallback((event?: FormEvent) => {
    event?.preventDefault();
    if (disabledReason || !meta) return;
    const text = composerText;
    if (/^\/research(?:\s|$)/i.test(text)) {
      setComposerText('');
      void openDeepResearch(text.replace(/^\/research\s*/i, ''));
      return;
    }
    setComposerText('');
    if (selectedSkill && localChat.isGeneralChat) {
      const skill = selectedSkill;
      setSelectedSkill(null);
      void api.prepareChatWorkspace({ objective: text, skill_name: skill.id })
        .then(async prepared => {
          const detail = await api.getPrivateChatWorkflow(prepared.workflow.id);
          await runWorkflowOnce(text, text, {
            yaml: detail.yaml,
            meta: chatMetaFromYaml(detail.yaml),
            workflowId: prepared.workflow.id,
          });
        })
        .catch(reason => {
          setComposerText(text);
          setSelectedSkill(skill);
          setControlError(reason instanceof Error ? reason.message : 'The selected skill could not be prepared.');
        });
      return;
    }
    if (localChat.isGeneralChat) {
      if (localChat.collectionId && localChat.ragAgentId && !workflowIsGeneralPreset) {
        void runWorkflowOnce(text, text);
        return;
      }
      if (!hasCompletedRun && !workflowIsGeneralPreset) {
        void runWorkflowOnce(text, text);
        return;
      }
      void api.planChatWorkspace({ objective: text })
        .then(async plan => {
          if (plan.kind === 'llm') {
            const workflow = await api.ensureGeneralChatWorkflow();
            const detail = await api.getPrivateChatWorkflow(workflow.id);
            await runWorkflowOnce(text, text, {
              yaml: detail.yaml,
              meta: chatMetaFromYaml(detail.yaml),
              workflowId: workflow.id,
            });
            return;
          }
          const prepared = await api.prepareChatWorkspace({ objective: text });
          const detail = await api.getPrivateChatWorkflow(prepared.workflow.id);
          await runWorkflowOnce(text, text, {
            yaml: detail.yaml,
            meta: chatMetaFromYaml(detail.yaml),
            workflowId: prepared.workflow.id,
          });
        })
        .catch(reason => {
          setComposerText(text);
          setControlError(reason instanceof Error ? reason.message : 'The request could not be routed to the required capability.');
        });
      return;
    }
    if (!hasCompletedRun) void runWorkflowOnce(text, text);
    else void askFollowUp(text, text);
  }, [askFollowUp, composerText, disabledReason, hasCompletedRun, localChat.collectionId, localChat.isGeneralChat, localChat.ragAgentId, meta, openDeepResearch, runWorkflowOnce, selectedSkill, workflowIsGeneralPreset]);

  const askWithoutKnowledge = useCallback(async (question: string) => {
    setControlError(null);
    try {
      const workflow = await api.ensureGeneralChatWorkflow();
      const detail = await api.getPrivateChatWorkflow(workflow.id);
      await runWorkflowOnce(question, question, {
        yaml: detail.yaml,
        meta: chatMetaFromYaml(detail.yaml),
        workflowId: workflow.id,
        responseLabel: 'General answer · not grounded in selected Knowledge',
      });
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : 'General Chat could not answer this question.');
    }
  }, [runWorkflowOnce]);

  function removeKnowledgeScope() {
    const updated = updateLocalChat(localChat.id, { collectionId: null, ragAgentId: null });
    if (updated) setLocalChat(updated);
    setKnowledgeScope(null);
  }

  function chooseSlashCommand(command: SlashCommand) {
    const result = applySlashCommand(command, composerText);
    const naturalFormat = result.format === 'table'
      ? 'Present the answer as a table. '
      : result.format === 'chart'
        ? 'Use a chart when the data supports it, with a text fallback. '
        : result.format === 'bullets'
          ? 'Use concise bullet points. '
          : result.format === 'prose'
            ? 'Use clear prose. '
            : '';
    setComposerText(`${naturalFormat}${result.text}`);
    if (result.action === 'templates') setTemplatesOpen(true);
    if (result.action === 'workflows') navigate('/chat');
    if (result.action === 'research') void openDeepResearch(result.text);
  }
  const slashMatches = matchingSlashCommands(composerText);


  if (loadError) {
    return (
      <div className="mx-auto max-w-3xl px-6 py-10">
        <p className="text-sm text-bad">
          This workflow could not be opened: {loadError}
        </p>
        <button
          type="button"
          onClick={() => setLoadAttempt(attempt => attempt + 1)}
          className="mt-4 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white"
        >
          Try again
        </button>
        <button
          type="button"
          onClick={() => navigate('/chat')}
          className="ml-2 mt-4 rounded-md border border-slate-300 px-3 py-1.5 text-sm"
        >
          ← Back to Chat
        </button>
      </div>
    );
  }

  if (!meta) {
    return <div className="px-6 py-10 text-sm text-ink-500">Loading workflow…</div>;
  }

  const title = meta.chatbotName ?? resourceName;
  const activityItems = Object.values(contextActivities);
  const createMenuItems: ComposerMenuItem[] = CREATE_OPTIONS.map(item => ({ ...item }));

  function openNoteDraft(titleValue: string, body: string) {
    setNoteDraft(createNote(titleValue, body));
    setNoteEditorOpen(true);
  }

  function saveNoteDraft(titleValue: string, body: string) {
    const note = noteDraft
      ? { ...noteDraft, title: titleValue.trim() || 'Untitled note', body: body.trim(), updatedAt: new Date().toISOString() }
      : createNote(titleValue, body);
    setNotes(current => [...current.filter(item => item.id !== note.id), note]);
    setNoteDraft(null);
    setNoteEditorOpen(false);
  }

  function openCitation(item: CitationTarget) {
    setCitation(item);
    const matching = workspaceSources.find(source => (
      (item.documentId && source.documentId === item.documentId)
      || source.title.toLowerCase() === item.title.toLowerCase()
    ));
    if (matching) {
      setHighlightedSourceId(matching.id);
      setWorkspaceSources(current => current.map(source => source.id === matching.id ? { ...source, referenced: true } : source));
      setSourcesCollapsed(false);
      setMobilePanel('sources');
    }
  }

  function selectActivity(nodeId: string, tab: SessionTab = 'activity') {
    setSelectedActivityNodeId(nodeId);
    setSessionTab(tab);
    setSessionsCollapsed(false);
    setMobilePanel('session');
  }

  function activitiesForRun(runId: string | null | undefined): AgentActivity[] {
    if (!runId || !meta) return [];
    return meta.nodes.flatMap(node => {
      const activity = activities[activityKey(runId, node.id)];
      return activity ? [activity] : [];
    });
  }

  function showSourceUsage(source: WorkspaceSource) {
    setHighlightedSourceId(source.id);
    setSessionTab('sources');
    setSessionsCollapsed(false);
    setMobilePanel('session');
  }

  function removeWorkspaceSource(source: WorkspaceSource) {
    setWorkspaceSources(current => current.filter(item => item.id !== source.id));
    if (source.file) {
      setAttachments(current => current.filter(file => file.file_id !== source.file?.file_id));
    }
    if (highlightedSourceId === source.id) setHighlightedSourceId(null);
  }

  function selectSourceFromSession(sourceId: string) {
    setHighlightedSourceId(sourceId);
    setSourcesCollapsed(false);
    setMobilePanel('sources');
  }

  function openTechnicalExecution(nodeId?: string | null) {
    if (!runDetail?.run_id) return;
    navigate(`/cockpit/${encodeURIComponent(runDetail.run_id)}`, {
      state: {
        attach: true,
        workflowYaml: runDetail.workflow_yaml ?? yamlText ?? undefined,
        workflowName: runDetail.workflow_name,
        selectedNodeId: nodeId ?? undefined,
      },
    });
  }

  return (
    <div className="chat-workspace chat-active-conversation">
      <ChatWorkspaceShell
        sources={<NotebookSourcesPanel
          sources={workspaceSources}
          notes={notes}
          collapsed={sourcesCollapsed}
          loading={false}
          highlightedSourceId={highlightedSourceId}
          onCollapse={() => setSourcesCollapsed(value => !value)}
          onToggle={sourceId => setWorkspaceSources(current => current.map(item => item.id === sourceId ? { ...item, selected: !item.selected } : item))}
          onToggleAll={selected => setWorkspaceSources(current => current.map(item => ({ ...item, selected: selected && ['ready', 'synced', 'outdated'].includes(item.status) })))}
          onAddSources={() => setSourcePickerOpen(true)}
          onOpenSource={source => { setHighlightedSourceId(source.id); if (source.sourceUrl) window.open(source.sourceUrl, '_blank', 'noopener,noreferrer'); }}
          onShowUsage={showSourceUsage}
          onRemoveSource={removeWorkspaceSource}
          onFilesDropped={files => void uploadAttachments(files)}
          onOpenNote={note => { setNoteDraft(note); setNoteEditorOpen(true); }}
          onNewNote={() => { setNoteDraft(null); setNoteEditorOpen(true); }}
        />}
        conversation={<main className="chat-active-center">
      <div className="mx-auto flex h-full min-w-0 max-w-4xl flex-1 flex-col px-4 py-4 sm:px-6">
      <header className="chat-conversation-header">
        <div>
          <h1 className="text-lg font-semibold text-ink-900">{localChat.title === 'New chat' ? title : localChat.title}</h1>
          <p>{title} · {activeSourceCount} active source{activeSourceCount === 1 ? '' : 's'} · {runDetail?.status ?? 'Ready'}</p>
        </div>
        <div className="chat-conversation-header-actions">
          <button type="button" onClick={() => { setSessionsCollapsed(false); setMobilePanel('session'); }}>Session</button>
          <button type="button" onClick={() => setHistoryOpen(true)}>Chats</button>
          <button type="button" onClick={startLocalChat}>New chat</button>
          <button type="button" aria-pressed={distractionFree} onClick={() => setDistractionFree(value => !value)} title="Toggle distraction-free mode">{distractionFree ? 'Show panels' : 'Focus'}</button>
        </div>
      </header>

      {!localChat.isGeneralChat && runDetail && runDetail.run_id === currentRunId && (
        <RunControlBar
          run={runDetail}
          pausePending={pausePending}
          actionBusy={controlBusy}
          error={controlError}
          onPause={() => void pauseCurrentRun()}
          onResume={() => void resumeCurrentRun()}
          onRetry={() => void startNewAttempt('retry')}
          onRestart={() => void startNewAttempt('restart')}
        />
      )}

      <div ref={scrollRef} className="mt-4 flex-1 space-y-4 overflow-y-auto pr-1">
        {messages.length === 0 && (
          <div className="rounded-lg border border-slate-200 bg-white p-5 shadow-sm">
            <div className="text-sm font-medium text-ink-900">{title}</div>
            <p className="mt-1 whitespace-pre-wrap text-sm text-ink-600">
              {meta.welcomeMessage ?? 'Send a message to run this workflow.'}
            </p>
            {meta.suggestedQuestions.length > 0 && (
              <div className="mt-4 flex flex-wrap gap-2">
                {meta.suggestedQuestions.map(prompt => (
                  <button
                    key={prompt}
                    type="button"
                    onClick={() => { setComposerText(prompt); }}
                    className="rounded-full border border-accent-200 bg-accent-50 px-3 py-1 text-xs text-accent-800 hover:bg-accent-100"
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}
        {messages.map((message, index) => {
          const precedingQuestion = [...messages.slice(0, index)].reverse().find(item => item.role === 'user');
          const runActivities = message.role === 'user' ? activitiesForRun(message.runId) : [];
          return <div key={message.id} className="chat-transcript-entry">
            <MessageView
              message={message}
              onInterventionResult={onInterventionResult}
              onOpenCitation={openCitation}
              onSaveAnswer={(titleValue, body) => openNoteDraft(titleValue, body)}
              onBroadenQuestion={question => setComposerText(`Answer this more broadly using the selected Knowledge: ${question}`)}
              onChooseSources={() => setMobilePanel('sources')}
              onAskWithoutKnowledge={question => void askWithoutKnowledge(question)}
              precedingQuestion={precedingQuestion?.role === 'user' ? precedingQuestion.text : ''}
              knowledgeBound={Boolean(localChat.collectionId && localChat.ragAgentId)}
              canRetry={message.role === 'error'
                && Boolean(message.runId)
                && message.runId === currentRunId
                && runDetail?.status === 'failed'
                && Boolean(runDetail.retry_available)}
              retryBusy={controlBusy === 'retry'}
              onRetry={() => void startNewAttempt('retry')}
            />
            {message.role === 'user' && runActivities.length > 0 && <AgentActivityGroup activities={runActivities} selectedNodeId={selectedActivityNodeId} onSelectNode={nodeId => selectActivity(nodeId)} />}
          </div>;
        })}
      </div>

      <form onSubmit={submit} className="mt-4 border-t border-slate-200 pt-3">
        {knowledgeScope && (
          <div className="chat-knowledge-scope" aria-label="Active Knowledge scope">
            <strong>Knowledge</strong>
            <span>{knowledgeScope.collection} · {knowledgeScope.agent}</span>
            <button type="button" aria-label="Remove Knowledge scope" onClick={removeKnowledgeScope}>×</button>
          </div>
        )}
        {activeSources.length > 0 && <div className="chat-context-chips" aria-label="Active context"><strong>Active context</strong>{activeSources.slice(0, 5).map(source => <button type="button" key={source.id} onClick={() => { setHighlightedSourceId(source.id); setSourcesCollapsed(false); setMobilePanel('sources'); }}>{source.title}<span onClick={event => { event.stopPropagation(); setWorkspaceSources(current => current.map(item => item.id === source.id ? { ...item, selected: false } : item)); }} aria-hidden>×</span></button>)}{activeSources.length > 5 && <button type="button" onClick={() => { setSourcesCollapsed(false); setMobilePanel('sources'); }}>+{activeSources.length - 5} sources</button>}</div>}
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => setTemplatesOpen(true)} className="rounded-md border border-slate-300 px-2 py-1 text-xs text-ink-700">Templates</button>
          <button type="button" onClick={() => void openDeepResearch(composerText)} className="rounded-md border border-slate-300 px-2 py-1 text-xs text-ink-700">Deep research</button>
        </div>
        {attachments.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {attachments.map(file => (
              <span key={file.file_id} className="inline-flex max-w-xs items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs text-ink-700">
                <span>{file.category === 'image' ? '🖼️' : '📄'}</span>
                <span className="truncate">{file.name}</span>
                <button type="button" aria-label={`Remove ${file.name}`} onClick={() => setAttachments(current => current.filter(item => item.file_id !== file.file_id))} className="text-ink-400 hover:text-bad">×</button>
              </span>
            ))}
          </div>
        )}
        <div
          className="relative rounded-xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-accent-400 focus-within:ring-2 focus-within:ring-accent-100"
          onDragOver={event => { if (meta.allowAttachments) event.preventDefault(); }}
          onDrop={event => { if (!meta.allowAttachments) return; event.preventDefault(); void uploadAttachments(Array.from(event.dataTransfer.files)); }}
        >
          {slashMatches.length > 0 && <div className="absolute bottom-full left-0 z-20 mb-2 w-72 overflow-hidden rounded-lg border border-slate-200 bg-white shadow-lg">{slashMatches.map(command => <button key={command.command} type="button" onClick={() => chooseSlashCommand(command)} className="block w-full px-3 py-2 text-left hover:bg-slate-50"><span className="text-xs font-medium text-accent-700">{command.command}</span><span className="ml-2 text-xs text-ink-700">{command.label}</span><span className="block text-[10px] text-ink-400">{command.description}</span></button>)}</div>}
          {composerMenu === 'skill' && (
            <ComposerMenu label="Choose a skill" items={skills} onClose={() => setComposerMenu(null)} onChoose={item => {
              setSelectedSkill(item);
              setComposerMenu(null);
            }} />
          )}
          {composerMenu === 'create' && (
            <ComposerMenu label="Create something" items={createMenuItems} onClose={() => setComposerMenu(null)} onChoose={item => { setCreateKind(item.id as CreateArtifactKind); setComposerMenu(null); }} />
          )}
          <textarea
            value={composerText}
            onChange={e => setComposerText(e.target.value)}
            onPaste={event => {
              const images = [...event.clipboardData.items]
                .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
                .flatMap(item => item.getAsFile() ? [item.getAsFile() as File] : []);
              if (images.length > 0 && meta.allowAttachments) {
                event.preventDefault();
                void uploadAttachments(images);
                return;
              }
              addWebSources(event.clipboardData.getData('text/plain'));
            }}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder="Ask anything about your sources…"
            disabled={Boolean(disabledReason)}
            className="block w-full resize-none border-0 bg-transparent px-2 py-2 text-sm outline-none disabled:bg-slate-50"
          />
          <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <div className="flex flex-wrap items-center gap-1">
              {meta.allowAttachments && <button type="button" onClick={() => fileInputRef.current?.click()} disabled={busy} className="rounded-md px-2 py-1.5 text-xs text-ink-600 hover:bg-slate-100 disabled:opacity-50">+ Attach</button>}
              <button type="button" aria-expanded={composerMenu === 'skill'} onClick={() => setComposerMenu(value => value === 'skill' ? null : 'skill')} className="rounded-md px-2 py-1.5 text-xs text-ink-600 hover:bg-slate-100">{selectedSkill ? `@ ${selectedSkill.label}` : '@ Skill'}</button>
              <button type="button" aria-expanded={composerMenu === 'create'} onClick={() => setComposerMenu(value => value === 'create' ? null : 'create')} className="rounded-md px-2 py-1.5 text-xs text-ink-600 hover:bg-slate-100">/ Create</button>
              {typeof window !== 'undefined' && speechRecognitionSupported() && (
                <button
                  type="button"
                  onClick={toggleDictation}
                  disabled={busy}
                  aria-pressed={listening}
                  className={`rounded-md px-2 py-1.5 text-xs disabled:opacity-50 ${listening ? 'bg-red-100 font-medium text-red-700' : 'text-ink-600 hover:bg-slate-100'}`}
                >
                  {listening ? '■ Stop dictation' : '🎙 Dictate'}
                </button>
              )}
              <input ref={fileInputRef} type="file" multiple className="sr-only" onChange={event => { void uploadAttachments(Array.from(event.target.files ?? [])); event.target.value = ''; }} />
            </div>
            <button type="submit" disabled={Boolean(disabledReason)} className="rounded-lg bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">Send ↑</button>
          </div>
          <p className="chat-execution-summary">{title} · {activeSourceCount} source{activeSourceCount === 1 ? '' : 's'} · {meta.capabilities.web ? 'Web available' : 'Workflow tools'}{selectedSkill ? ` · ${selectedSkill.label}` : ''}</p>
        </div>
        {attachmentError && <p className="mt-1 text-xs text-bad">{attachmentError}</p>}
        {speechError && <p className="mt-1 text-xs text-bad">{speechError}</p>}
        {transcriptSyncError && (
          <p className="mt-1 text-xs text-bad" role="alert">
            Transcript sync failed: {transcriptSyncError}. New messages may not survive refresh.
          </p>
        )}
        {disabledReason && <p className="mt-1 text-xs text-ink-500">{disabledReason}</p>}
      </form>
      </div>
      </main>}
        session={<SessionAuditPanel title={localChat.title === 'New chat' ? title : localChat.title} collapsed={sessionsCollapsed} run={runDetail} audit={auditEvents} activities={activityItems} sources={workspaceSources} messageCount={messages.length} workflowLabel={title} activeTab={sessionTab} selectedNodeId={selectedActivityNodeId} onCollapse={() => setSessionsCollapsed(value => !value)} onOpenHistory={() => setHistoryOpen(true)} onNewChat={startLocalChat} onTabChange={setSessionTab} onSelectNode={nodeId => { setSelectedActivityNodeId(nodeId); }} onSelectSource={selectSourceFromSession} onOpenTechnical={openTechnicalExecution} />}
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
      <CitationDrawer
        citation={citation}
        onClose={() => setCitation(null)}
        onSave={item => {
          setNotes(current => [...current, createNote(item.title, item.snippet ?? item.title)]);
          setCitation(null);
        }}
        onAsk={item => {
          setComposerText(`What does this passage mean in context?\n\n${item.snippet ?? item.title}`);
          setCitation(null);
        }}
      />
      <SourcePickerDialog
        open={sourcePickerOpen}
        sourceCount={workspaceSources.length}
        onClose={() => setSourcePickerOpen(false)}
        onUpload={files => uploadAttachments(files)}
        onAddUrls={addWebSources}
        onImportDrive={importDriveFiles}
      />
      <NoteEditor
        note={noteDraft}
        open={noteEditorOpen}
        onClose={() => { setNoteEditorOpen(false); setNoteDraft(null); }}
        onSave={saveNoteDraft}
      />
      <ArtifactCreationDrawer
        kind={createKind}
        sourceCount={activeSourceCount}
        onClose={() => setCreateKind(null)}
        onGenerate={prompt => { setComposerText(prompt); setCreateKind(null); setMobilePanel('chat'); }}
      />
      <ChatHistoryDrawer open={historyOpen} chats={loadLocalChatHistory().chats} activeChatId={localChat.id} onClose={() => setHistoryOpen(false)} onNew={startLocalChat} onOpen={openLocalChat} onRename={chat => { const nextTitle = window.prompt('Rename chat', chat.title)?.trim(); if (!nextTitle) return; const updated = updateLocalChat(chat.id, { title: nextTitle }); if (updated?.id === localChat.id) setLocalChat(updated); setHistoryRevision(value => value + 1); }} onDelete={chat => { if (!window.confirm(`Delete “${chat.title}” from this browser?`)) return; const next = deleteLocalChat(chat.id) ?? createLocalChat(); setHistoryRevision(value => value + 1); if (chat.id === localChat.id) openLocalChat(next); }} key={historyRevision} />
      {templatesOpen && <PromptTemplateLibrary onClose={() => setTemplatesOpen(false)} onInsert={text => setComposerText(text)} />}
    </div>
  );
}


// ---- Message rendering --------------------------------------------------

function MessageView({
  message,
  onInterventionResult,
  onOpenCitation,
  onSaveAnswer,
  canRetry,
  retryBusy,
  onRetry,
  precedingQuestion,
  knowledgeBound,
  onBroadenQuestion,
  onChooseSources,
  onAskWithoutKnowledge,
}: {
  message: ChatMessage;
  onInterventionResult: (result: unknown, messageId: string, decision?: string) => void;
  onOpenCitation: (citation: CitationTarget) => void;
  onSaveAnswer: (title: string, body: string) => void;
  canRetry: boolean;
  retryBusy: boolean;
  onRetry: () => void;
  precedingQuestion: string;
  knowledgeBound: boolean;
  onBroadenQuestion: (question: string) => void;
  onChooseSources: () => void;
  onAskWithoutKnowledge: (question: string) => void;
}) {
  if (message.role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-xl bg-accent-600 px-4 py-2.5 text-sm text-white shadow-sm">
          <p className="whitespace-pre-wrap">{message.text}</p>
          {message.attachments && message.attachments.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5 border-t border-white/20 pt-2">
              {message.attachments.map(file => (
                <span key={file.file_id} className="rounded-md bg-white/15 px-2 py-1 text-xs">📎 {file.name}</span>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (message.role === 'attempt') {
    return (
      <div className="flex items-center gap-3 py-1" role="separator">
        <div className="h-px flex-1 bg-slate-200" />
        <span className="text-[11px] font-medium text-ink-400">{message.text}</span>
        <div className="h-px flex-1 bg-slate-200" />
      </div>
    );
  }

  if (message.role === 'error') {
    const projection = friendlyError(message.text);
    return (
      <div className="chat-friendly-error" role="alert">
        <strong>{projection.title}</strong>
        <p>{projection.message}</p>
        {canRetry && projection.actions.includes('Retry Knowledge search') && (
          <button type="button" className="chat-error-action" disabled={retryBusy} onClick={onRetry}>
            {retryBusy ? 'Retrying Knowledge search…' : 'Retry Knowledge search'}
          </button>
        )}
        <details>
          <summary>Technical details</summary>
          <pre>{message.text}</pre>
        </details>
      </div>
    );
  }

  if (message.role === 'intervention') {
    return (
      <ChatInterventionCard
        message={message}
        onResult={(result, decision) => onInterventionResult(result, message.id, decision)}
      />
    );
  }

  const citations = message.segments.flatMap(segment => segment.kind === 'sources' ? segment.items : []);
  const answerText = message.segments.flatMap(segment => segment.kind === 'text' ? [segment.text] : []).join('\n\n');
  const noKnowledgeEvidence = knowledgeBound
    && citations.length === 0
    && /(?:no supporting information was found|could not find supporting sources)/i.test(answerText);
  const downloadJson = () => {
    if (message.structuredResult === undefined) return;
    const blob = new Blob([JSON.stringify(message.structuredResult, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${message.runId ?? 'workflow-result'}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div className="flex justify-start">
      <div className="chat-grounded-answer max-w-[90%] space-y-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
        {message.responseLabel && <div className="chat-response-label">{message.responseLabel}</div>}
        {message.segments.map((segment, index) => (
          <SegmentView
            key={index}
            segment={segment}
            citations={citations}
            onOpenCitation={onOpenCitation}
          />
        ))}
        {noKnowledgeEvidence && precedingQuestion && (
          <div className="chat-no-evidence-actions">
            <button type="button" onClick={() => onBroadenQuestion(precedingQuestion)}>Broaden the question</button>
            <button type="button" onClick={onChooseSources}>Choose other sources</button>
            <button type="button" onClick={() => onAskWithoutKnowledge(precedingQuestion)}>Ask without Knowledge</button>
          </div>
        )}
        <div className="chat-answer-actions">
          {answerText && <CopyButton text={answerText} />}
          {answerText && <button type="button" onClick={() => onSaveAnswer('Saved answer', answerText)}>Save note</button>}
          {message.structuredResult !== undefined && <button type="button" onClick={downloadJson}>Download JSON</button>}
          {citations.length > 0 && <span>{citations.length} source{citations.length === 1 ? '' : 's'}</span>}
        </div>
        {typeof window !== 'undefined' && speechSynthesisSupported() && (
          <div className="flex gap-3 text-[11px] text-ink-400">
            <button type="button" onClick={() => speakText(assistantSpeechText(message.segments))}>🔊 Read aloud</button>
            <button type="button" onClick={() => stopSpeaking()}>Stop</button>
          </div>
        )}
      </div>
    </div>
  );
}

function assistantSpeechText(segments: AssistantSegment[]): string {
  return segments.flatMap(segment => {
    if (segment.kind === 'text') return [segment.text];
    if (segment.kind === 'code') return [`${segment.language ?? 'Code'} snippet`];
    if (segment.kind === 'sources') return [`Sources: ${segment.items.map(item => item.title).join(', ')}`];
    return [segment.title];
  }).join('. ');
}

function SegmentView({
  segment,
  citations,
  onOpenCitation,
}: {
  segment: AssistantSegment;
  citations: CitationTarget[];
  onOpenCitation: (citation: CitationTarget) => void;
}) {
  if (segment.kind === 'text') {
    const parts = segment.text.split(/(\[\d+\])/g);
    return <p className="whitespace-pre-wrap text-sm text-ink-800">{parts.map((part, index) => {
      const match = /^\[(\d+)\]$/.exec(part);
      if (!match) return part;
      const number = Number(match[1]);
      const citationItem = citations.find(item => item.number === number);
      if (!citationItem) return part;
      return <button key={`${part}-${index}`} type="button" aria-label={`Open source ${number}: ${citationItem.title}`} onClick={() => onOpenCitation(citationItem)} className="chat-citation-chip">{part}</button>;
    })}</p>;
  }
  if (segment.kind === 'code') {
    return (
      <div className="overflow-hidden rounded-lg border border-slate-700 bg-slate-950 text-slate-100">
        <div className="flex items-center justify-between border-b border-slate-700 px-3 py-2">
          <span className="text-xs font-medium text-slate-300">{segment.filename ?? segment.language ?? 'Code'}</span>
          <CopyButton text={segment.code} className="border-slate-600 bg-slate-800 text-slate-100 hover:bg-slate-700" />
        </div>
        <pre className="overflow-x-auto p-3 text-xs leading-5"><code>{segment.code}</code></pre>
      </div>
    );
  }
  if (segment.kind === 'sources') {
    return (
      <div className="chat-answer-sources">
        <div>Sources</div>
        <div>
          {segment.items.map(item => (
            <button type="button" key={`${item.documentId ?? item.title}:${item.chunkId ?? item.number}`} onClick={() => onOpenCitation(item)}>
              <span>[{item.number}]</span>
              <strong>{item.title}</strong>
              <small>{[item.page ? `Page ${item.page}` : null, item.section].filter(Boolean).join(' · ') || 'View source passage'}</small>
              {item.snippet && <p>{item.snippet}</p>}
              <em>{item.evidenceStatus === 'retrieved_not_verified' ? 'Retrieved · not independently verified' : item.evidenceStatus === 'acquired_full_text' ? 'Full text acquired' : 'Candidate source'}</em>
            </button>
          ))}
        </div>
      </div>
    );
  }
  return <ArtifactCard artifact={segment} />;
}

function formatBytes(value?: number): string | null {
  if (value === undefined) return null;
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} KB`;
}

function artifactDetails(artifact: ChatArtifact): string[] {
  const details = [formatBytes(artifact.byteSize)];
  if (artifact.pageCount !== undefined) details.push(`${artifact.pageCount} page${artifact.pageCount === 1 ? '' : 's'}`);
  else if (artifact.estimatedPageCount !== undefined) details.push(`~${artifact.estimatedPageCount} pages`);
  if (artifact.slideCount !== undefined) details.push(`${artifact.slideCount} slide${artifact.slideCount === 1 ? '' : 's'}`);
  if (artifact.sheetCount !== undefined) details.push(`${artifact.sheetCount} sheet${artifact.sheetCount === 1 ? '' : 's'}`);
  if (artifact.rowCount !== undefined) details.push(`${artifact.rowCount} row${artifact.rowCount === 1 ? '' : 's'}`);
  return details.filter((value): value is string => Boolean(value));
}

function ArtifactCard({ artifact }: { artifact: ChatArtifact }) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    if (artifact.kind !== 'image' || !artifact.reference) return undefined;
    let cancelled = false;
    let objectUrl: string | null = null;
    const params = new URLSearchParams({ key: artifact.reference.minio_key });
    fetch(`${apiBase()}/api/workflow-input-files/content?${params.toString()}`, {
      credentials: 'include',
      headers: getAuthHeaders(),
    })
      .then(response => (response.ok ? response.blob() : null))
      .then(blob => {
        if (cancelled || !blob) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [artifact]);

  const download = () => {
    if (artifact.reference) void api.downloadWorkflowFile(artifact.reference);
    else void api.downloadArtifact(artifact.key);
  };

  if (artifact.kind === 'image') {
    const src = artifact.reference ? imageUrl : api.fileUrl(artifact.key);
    return (
      <figure className="max-w-md rounded-md border border-slate-200 p-2">
        {src
          ? <img src={src} alt={artifact.title} className="max-h-96 rounded object-contain" />
          : <div className="py-6 text-center text-xs text-ink-500">{artifact.title}</div>}
        <figcaption className="mt-1 flex items-center justify-between gap-2 text-xs text-ink-600">
          <span className="truncate">
            {artifact.title}{artifact.provider ? ` · ${artifact.provider}` : ''}{artifact.model ? ` · ${artifact.model}` : ''}
          </span>
          <button type="button" onClick={download} className="text-accent-700 underline">
            Download
          </button>
        </figcaption>
      </figure>
    );
  }

  const labels: Record<Exclude<ChatArtifact['kind'], 'image'>, { icon: string; label: string; style: string }> = {
    pdf: { icon: 'PDF', label: 'PDF document', style: 'border-red-200 bg-red-50 text-red-800' },
    docx: { icon: 'DOCX', label: 'Word document', style: 'border-blue-200 bg-blue-50 text-blue-800' },
    pptx: { icon: 'PPTX', label: 'Presentation', style: 'border-orange-200 bg-orange-50 text-orange-800' },
    xlsx: { icon: 'XLSX', label: 'Spreadsheet', style: 'border-emerald-200 bg-emerald-50 text-emerald-800' },
  };
  const presentation = labels[artifact.kind];
  const details = artifactDetails(artifact);
  return (
    <div className={`max-w-sm rounded-lg border p-3 ${presentation.style}`}>
      <div className="flex items-start gap-3">
        <span className="rounded bg-white/70 px-2 py-1 text-[10px] font-bold tracking-wide" aria-hidden>{presentation.icon}</span>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium opacity-75">{presentation.label}</div>
          <div className="truncate text-sm font-semibold">{artifact.title}</div>
          {details.length > 0 && <div className="mt-0.5 text-xs opacity-70">{details.join(' · ')}</div>}
        </div>
      </div>
      <div className="mt-3 flex gap-3 border-t border-current/10 pt-2 text-xs font-medium">
        {artifact.kind === 'pdf' && !artifact.reference && (
          <a href={api.fileUrl(artifact.key)} target="_blank" rel="noreferrer" className="underline">Open</a>
        )}
        <button type="button" onClick={download} className="underline">Download</button>
      </div>
      {artifact.kind === 'pdf' && !artifact.reference && (
        <iframe title={artifact.title} src={api.fileUrl(artifact.key)} className="mt-3 h-72 w-full rounded border border-slate-200 bg-white" />
      )}
    </div>
  );
}
