import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';

import { api, apiBase, getAuthHeaders } from '../../../api/client';
import type { ChatWorkspaceExperience, LLMModelInfo, NodeRun, PrivateChatWorkflowSummary, RunDetail, RunEvent, WorkflowFileReference, WorkflowSummary } from '../../../api/types';
import { HITLPanel } from '../HITLPanel';
import { CopyButton } from '../../../components/CopyButton';
import { AgentActivityCard } from './AgentActivityCard';
import { ChatNodeInspector } from './ChatNodeInspector';
import { WorkflowContextPanel, WorkflowExecutionStrip } from './WorkflowContextPanel';
import { RunControlBar } from './RunControlBar';
import { AddWorkflowDialog } from './AddWorkflowDialog';
import { PromptTemplateLibrary } from './PromptTemplateLibrary';
import {
  applySlashCommand, classifyResponseFormat, followUpExecutionOutput, formatHint, matchingSlashCommands,
  type ResponseFormat, type SlashCommand, type WritingStyle,
} from './chatEnhancements';
import { observeChatRun } from './observeChatRun';
import { attemptLabel, type RunControlAction } from './runControls';
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
  buildRunInputs,
  chatEligibleWorkflows,
  chatMetaFromYaml,
  compatibleTransformModels,
  composerDisabledReason,
  interventionFromPendingGate,
  resolveComposerIntent,
  withTransformModel,
  type AssistantSegment,
  type AgentActivity,
  type WorkflowChatMeta,
} from './businessChatModel';
import type { ChatArtifact } from './chatOutputs';
import {
  deserializeDurableMessage,
  serializeDurableMessage,
  type DurableChatMessage,
} from './chatTranscript';

/**
 * Business Chat: a published workflow experienced as a conversation.
 *
 * The first message runs the real workflow through the existing execution
 * API and SSE stream; a Human Intervention node switches the conversation
 * into the existing HITLPanel approval card (backed by the durable
 * pending-gate record, so a refresh restores it); follow-up messages ask
 * the existing Ask AI service about the run. Nothing here bypasses the
 * workflow runtime or re-implements retrieval, execution, or review.
 */

type ChatMessage = DurableChatMessage
  | { id: string; role: 'activity'; nodeId: string; activityKey: string };

function newId(): string {
  return `msg-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function activityKey(runId: string, nodeId: string): string {
  return `${runId}:${nodeId}`;
}

export function BusinessChat() {
  const { workflowName, chatWorkflowId } = useParams();
  if (!workflowName && !chatWorkflowId) return <BusinessChatHome />;
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

// ---- Home: pick a workflow to talk to --------------------------------

function BusinessChatHome() {
  const navigate = useNavigate();
  const [shared, setShared] = useState<WorkflowSummary[] | null>(null);
  const [personal, setPersonal] = useState<PrivateChatWorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [addOpen, setAddOpen] = useState(false);
  const [personalActionId, setPersonalActionId] = useState<string | null>(null);
  const [personalActionError, setPersonalActionError] = useState<string | null>(null);
  const [experiences, setExperiences] = useState<ChatWorkspaceExperience[]>([]);
  const [objective, setObjective] = useState('');
  const [experienceId, setExperienceId] = useState('');
  const [preferredOutput, setPreferredOutput] = useState<'auto' | 'text' | 'pdf' | 'pptx'>('auto');
  const [selectedWorkflow, setSelectedWorkflow] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [retrievalProfileId, setRetrievalProfileId] = useState('');
  const [ragAgentId, setRagAgentId] = useState('');
  const [integrationConnection, setIntegrationConnection] = useState('');
  const [integrationTool, setIntegrationTool] = useState('');
  const [workspaceFiles, setWorkspaceFiles] = useState<WorkflowFileReference[]>([]);
  const [workspaceBusy, setWorkspaceBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState<string | null>(null);
  const workspaceFileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api.listChatWorkflows(), api.listPrivateChatWorkflows(), api.listChatWorkspaceExperiences(),
    ])
      .then(([sharedItems, privateItems, workspaceExperiences]) => {
        if (cancelled) return;
        setShared(chatEligibleWorkflows(sharedItems));
        setPersonal(privateItems.workflows);
        setExperiences(workspaceExperiences.experiences);
      })
      .catch(err => { if (!cancelled) setError(err instanceof Error ? err.message : String(err)); });
    return () => { cancelled = true; };
  }, []);

  const visibleShared = (shared ?? []).filter(item => {
    if (query.trim() === '') return true;
    const haystack = `${item.name} ${item.description} ${item.library?.title ?? ''} ${item.use_case}`;
    return haystack.toLowerCase().includes(query.trim().toLowerCase());
  });
  const visiblePersonal = (personal ?? []).filter(item => (
    `${item.name} ${item.description} ${item.slug}`.toLowerCase().includes(query.trim().toLowerCase())
  ));
  const loaded = shared !== null && personal !== null;

  async function requestPublication(item: PrivateChatWorkflowSummary) {
    if (personalActionId) return;
    setPersonalActionId(item.id);
    setPersonalActionError(null);
    try {
      const updated = await api.requestPrivateChatWorkflowPublication(item.id);
      setPersonal(current => (current ?? []).map(existing => (
        existing.id === item.id ? updated : existing
      )));
    } catch (reason) {
      setPersonalActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPersonalActionId(null);
    }
  }

  async function archivePersonal(item: PrivateChatWorkflowSummary) {
    if (personalActionId) return;
    setPersonalActionId(item.id);
    setPersonalActionError(null);
    try {
      await api.archivePrivateChatWorkflow(item.id);
      setPersonal(current => (current ?? []).filter(existing => existing.id !== item.id));
    } catch (reason) {
      setPersonalActionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setPersonalActionId(null);
    }
  }

  async function uploadWorkspaceFiles(files: File[]) {
    if (files.length === 0) return;
    setWorkspaceBusy(true);
    setWorkspaceError(null);
    try {
      const uploaded = await api.uploadWorkflowFiles(files);
      setWorkspaceFiles(current => [
        ...current,
        ...uploaded.files.filter(file => !current.some(item => item.file_id === file.file_id)),
      ]);
    } catch (reason) {
      setWorkspaceError(reason instanceof Error ? reason.message : 'The files could not be uploaded.');
    } finally {
      setWorkspaceBusy(false);
      if (workspaceFileRef.current) workspaceFileRef.current.value = '';
    }
  }

  async function startWorkspace() {
    if (!objective.trim() || workspaceBusy) return;
    setWorkspaceBusy(true);
    setWorkspaceError(null);
    try {
      const prepared = await api.prepareChatWorkspace({
        objective: objective.trim(),
        experience_id: experienceId || null,
        selected_workflow: selectedWorkflow || null,
        preferred_output: preferredOutput,
        has_attachments: workspaceFiles.length > 0,
        attachment_categories: [...new Set(workspaceFiles.map(file => file.category))],
        collection_id: collectionId.trim() || null,
        retrieval_profile_id: retrievalProfileId.trim() || null,
        rag_agent_id: ragAgentId.trim() || null,
        integration_connection: integrationConnection.trim() || null,
        integration_tool: integrationTool.trim() || null,
      });
      if (workspaceFiles.length > 0) {
        window.sessionStorage.setItem(
          `eurskem.chat.pending-attachments:${prepared.workflow.id}`,
          JSON.stringify(workspaceFiles),
        );
      }
      navigate(`/chat/private/${encodeURIComponent(prepared.workflow.id)}?prompt=${encodeURIComponent(objective.trim())}`);
    } catch (reason) {
      setWorkspaceError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkspaceBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-4xl px-6 py-10">
      <section className="rounded-2xl border border-accent-200 bg-gradient-to-br from-white to-accent-50 p-5 shadow-sm">
        <div className="max-w-2xl">
          <h1 className="text-2xl font-semibold text-ink-900">What do you want to accomplish?</h1>
          <p className="mt-1 text-sm text-ink-500">Ask directly, attach files, select a Knowledge source, or choose an existing workflow. The workspace uses the smallest valid workflow path.</p>
        </div>
        <textarea
          value={objective}
          onChange={event => setObjective(event.target.value)}
          rows={4}
          placeholder="Analyze these documents, identify the key risks, and create an executive presentation…"
          className="mt-4 block w-full rounded-xl border border-slate-300 bg-white px-4 py-3 text-sm shadow-inner"
        />
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs font-medium text-ink-600">Experience
            <select aria-label="Workspace experience" value={experienceId} onChange={event => setExperienceId(event.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-xs">
              <option value="">Auto</option>
              {experiences.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-ink-600">Output
            <select aria-label="Workspace output" value={preferredOutput} onChange={event => setPreferredOutput(event.target.value as typeof preferredOutput)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-xs">
              <option value="auto">Auto</option><option value="text">Chat answer</option><option value="pdf">PDF</option><option value="pptx">Presentation</option>
            </select>
          </label>
          <label className="text-xs font-medium text-ink-600">Existing workflow
            <select aria-label="Existing workflow" value={selectedWorkflow} onChange={event => setSelectedWorkflow(event.target.value)} className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-xs">
              <option value="">Auto-select</option>
              {(shared ?? []).map(item => <option key={item.name} value={item.name}>{item.library?.title ?? item.name}</option>)}
            </select>
          </label>
          <label className="text-xs font-medium text-ink-600">Files
            <input ref={workspaceFileRef} type="file" multiple onChange={event => void uploadWorkspaceFiles(Array.from(event.target.files ?? []))} className="mt-1 block w-full text-xs" />
          </label>
        </div>
        <details className="mt-3 text-xs text-ink-600">
          <summary className="cursor-pointer font-medium">Use an indexed Knowledge source</summary>
          <div className="mt-2 grid gap-3 sm:grid-cols-3">
            <input aria-label="Knowledge collection ID" value={collectionId} onChange={event => setCollectionId(event.target.value)} placeholder="Collection ID" className="rounded-md border border-slate-300 px-3 py-2" />
            <input aria-label="Retrieval profile ID" value={retrievalProfileId} onChange={event => setRetrievalProfileId(event.target.value)} placeholder="Retrieval Profile ID" className="rounded-md border border-slate-300 px-3 py-2" />
            <input aria-label="RAG Agent ID" value={ragAgentId} onChange={event => setRagAgentId(event.target.value)} placeholder="Or saved RAG Agent ID" className="rounded-md border border-slate-300 px-3 py-2" />
          </div>
        </details>
        <details className="mt-3 text-xs text-ink-600">
          <summary className="cursor-pointer font-medium">Use a configured MCP or integration tool</summary>
          <div className="mt-2 grid gap-3 sm:grid-cols-2">
            <input aria-label="Integration connection ID" value={integrationConnection} onChange={event => setIntegrationConnection(event.target.value)} placeholder="MCP server / connection ID" className="rounded-md border border-slate-300 px-3 py-2" />
            <input aria-label="Integration tool name" value={integrationTool} onChange={event => setIntegrationTool(event.target.value)} placeholder="Tool name" className="rounded-md border border-slate-300 px-3 py-2" />
          </div>
        </details>
        {workspaceFiles.length > 0 && <div className="mt-3 flex flex-wrap gap-2">{workspaceFiles.map(file => <span key={file.file_id} className="rounded-full bg-white px-2 py-1 text-[11px] text-ink-600 ring-1 ring-slate-200">{file.name}</span>)}</div>}
        {workspaceError && <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-bad">{workspaceError}</p>}
        <button type="button" disabled={!objective.trim() || workspaceBusy} onClick={() => void startWorkspace()} className="mt-4 rounded-lg bg-accent-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-accent-700 disabled:opacity-50">
          {workspaceBusy ? 'Preparing workflow…' : 'Start in Chat →'}
        </button>
      </section>

      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="mt-10 text-xl font-semibold text-ink-900">Or open a workflow directly</h2>
          <p className="mt-1 text-sm text-ink-500">Power users can keep selecting the exact workflow.</p>
        </div>
        <button type="button" onClick={() => setAddOpen(true)} className="rounded-md bg-accent-600 px-4 py-2 text-sm font-medium text-white hover:bg-accent-700">+ Add workflow</button>
      </div>
      <input
        type="search"
        value={query}
        onChange={e => setQuery(e.target.value)}
        placeholder="Search workflows…"
        className="mt-5 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border"
      />
      {error && <p className="mt-4 text-sm text-bad">{error}</p>}
      {personalActionError && <p className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-bad">{personalActionError}</p>}
      {!loaded && !error && <p className="mt-6 text-sm text-ink-500">Loading workflows…</p>}
      {loaded && visibleShared.length === 0 && visiblePersonal.length === 0 && (
        <p className="mt-6 text-sm text-ink-500">
          No workflows match your search.
        </p>
      )}
      {loaded && <h2 className="mt-7 text-sm font-semibold text-ink-800">My workflows</h2>}
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {visiblePersonal.map(item => (
          <div key={item.id} className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm hover:border-accent-300 hover:shadow">
            <button type="button" onClick={() => navigate(`/chat/private/${encodeURIComponent(item.id)}`)} className="block w-full text-left">
              <div className="flex items-center justify-between gap-2"><div className="text-sm font-semibold text-ink-900">🔒 {item.name}</div><span className="text-[10px] uppercase tracking-wide text-ink-400">Private</span></div>
              <div className="mt-1 line-clamp-2 text-xs text-ink-500">{item.description}</div>
              {item.output_compatibility.detected_types.length > 0 && (
                <div className="mt-2 text-[10px] uppercase tracking-wide text-ink-400">
                  Output · {item.output_compatibility.detected_types.join(' · ')}
                </div>
              )}
              {item.status === 'publish_requested' && <div className="mt-2 text-[11px] text-amber-700">Publication requested</div>}
              <div className="mt-3 text-xs font-medium text-accent-700">Open →</div>
            </button>
            <div className="mt-3 flex items-center gap-3 border-t border-slate-100 pt-3 text-[11px]">
              {item.status === 'private' && (
                <button type="button" disabled={personalActionId !== null} onClick={() => void requestPublication(item)} className="font-medium text-accent-700 hover:underline disabled:opacity-50">
                  {personalActionId === item.id ? 'Requesting…' : 'Request publication'}
                </button>
              )}
              <button type="button" disabled={personalActionId !== null} onClick={() => void archivePersonal(item)} className="text-ink-500 hover:text-bad hover:underline disabled:opacity-50">
                {personalActionId === item.id ? 'Working…' : 'Archive'}
              </button>
            </div>
          </div>
        ))}
      </div>
      {visibleShared.length > 0 && <h2 className="mt-7 text-sm font-semibold text-ink-800">Shared workflows</h2>}
      <div className="mt-3 grid gap-4 sm:grid-cols-2">
        {visibleShared.map(item => (
          <button key={item.name} type="button" onClick={() => navigate(`/chat/shared/${encodeURIComponent(item.name)}`)} className="rounded-lg border border-slate-200 bg-white p-4 text-left shadow-sm hover:border-accent-300 hover:shadow">
            <div className="text-sm font-semibold text-ink-900">{item.library?.title ?? item.name}</div>
            <div className="mt-1 line-clamp-2 text-xs text-ink-500">{item.library?.summary ?? item.description}</div>
            <div className="mt-3 text-xs font-medium text-accent-700">Open →</div>
          </button>
        ))}
      </div>
      {addOpen && <AddWorkflowDialog onClose={() => setAddOpen(false)} onCreated={item => { setPersonal(current => [item, ...(current ?? [])]); setAddOpen(false); navigate(`/chat/private/${encodeURIComponent(item.id)}`); }} />}
    </div>
  );
}

// ---- Conversation ------------------------------------------------------

function BusinessChatConversation({ workflowId, source }: { workflowId: string; source: 'shared' | 'private' }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [yamlText, setYamlText] = useState<string | null>(null);
  const [resourceName, setResourceName] = useState(workflowId);
  const [meta, setMeta] = useState<WorkflowChatMeta | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [transcriptSyncError, setTranscriptSyncError] = useState<string | null>(null);
  const [composerText, setComposerText] = useState('');
  const [templatesOpen, setTemplatesOpen] = useState(false);
  const [responseFormat, setResponseFormat] = useState<ResponseFormat>('auto');
  const [writingStyle, setWritingStyle] = useState<WritingStyle>('concise');
  const [composerMode, setComposerMode] = useState<'auto' | 'ask' | 'run'>('auto');
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [askBusy, setAskBusy] = useState(false);
  const [currentRunId, setCurrentRunId] = useState<string | null>(null);
  const [hasCompletedRun, setHasCompletedRun] = useState(false);
  const [activities, setActivities] = useState<Record<string, AgentActivity>>({});
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [pausePending, setPausePending] = useState(false);
  const [controlBusy, setControlBusy] = useState<RunControlAction | null>(null);
  const [controlError, setControlError] = useState<string | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [contextOpen, setContextOpen] = useState(() => window.innerWidth >= 640);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [attachments, setAttachments] = useState<WorkflowFileReference[]>([]);
  const [uploading, setUploading] = useState(false);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [models, setModels] = useState<LLMModelInfo[]>([]);
  const [modelsError, setModelsError] = useState<string | null>(null);
  const [selectedModel, setSelectedModel] = useState('workflow_default');
  const [listening, setListening] = useState(false);
  const [speechError, setSpeechError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const runSubmissionRef = useRef(false);
  const finalizedRunsRef = useRef(new Set<string>());
  const recognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const contextManuallyToggledRef = useRef(false);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const syncContextPanel = () => {
      if (!contextManuallyToggledRef.current) setContextOpen(window.innerWidth >= 640);
    };
    window.addEventListener('resize', syncContextPanel);
    return () => window.removeEventListener('resize', syncContextPanel);
  }, []);

  useEffect(() => {
    const prompt = searchParams.get('prompt');
    if (!prompt) return;
    setComposerText(prompt);
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

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
    let cancelled = false;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setLoadError(null);
    const workflowLoad = source === 'private'
      ? api.getPrivateChatWorkflow(workflowId, controller.signal)
          .then(item => ({ yaml: item.yaml, name: item.name }))
      : api.getWorkflow(workflowId, controller.signal)
          .then(item => ({ yaml: item.yaml, name: item.name }));
    Promise.all([
      workflowLoad,
      api.resolveBusinessChatConversation(source, workflowId),
    ])
      .then(([{ yaml, name }, transcript]) => {
        if (cancelled) return;
        const restored = transcript.messages
          .map(deserializeDurableMessage)
          .filter((message): message is DurableChatMessage => message !== null);
        setYamlText(yaml);
        setResourceName(name);
        setMeta(chatMetaFromYaml(yaml));
        setConversationId(transcript.conversation.id);
        setMessages(restored);
        const latestRunMessage = [...restored].reverse().find(message => (
          'runId' in message && message.runId
        ) || message.role === 'intervention');
        const latestRunId = latestRunMessage?.role === 'intervention'
          ? latestRunMessage.request.runId
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
  }, [workflowId, source, loadAttempt]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages]);

  useEffect(() => () => {
    abortRef.current?.abort();
    recognitionRef.current?.abort();
    stopSpeaking();
  }, []);

  useEffect(() => {
    if (!meta?.capabilities.models) return;
    let cancelled = false;
    api.llmModels()
      .then(response => { if (!cancelled) setModels(response.models.filter(model => model.enabled)); })
      .catch(error => {
        if (!cancelled) setModelsError(error instanceof Error ? error.message : 'Model choices are unavailable.');
      });
    return () => { cancelled = true; };
  }, [meta?.capabilities.models]);

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

  const selectNode = useCallback((nodeId: string, openInspector = true) => {
    setSelectedNodeId(nodeId);
    setInspectorOpen(openInspector);
    window.requestAnimationFrame(() => {
      document.querySelector(`[data-node-id="${CSS.escape(nodeId)}"]`)?.scrollIntoView({
        behavior: 'smooth', block: 'center',
      });
    });
  }, []);

  const hydrateRun = useCallback(async (runId: string) => {
    const { run } = await api.runDetail(runId);
    setRunDetail(run);
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

  const ensureActivityMessage = useCallback((runId: string, nodeId: string) => {
    const key = activityKey(runId, nodeId);
    setMessages(current => current.some(message => message.role === 'activity' && message.activityKey === key)
      ? current
      : [...current, { id: `activity-${runId}-${nodeId}`, role: 'activity', nodeId, activityKey: key }]);
  }, []);

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
      appendDurableMessage({
        id: `run-result-${runId}`,
        role: 'assistant',
        segments: assistantSegments(run),
        runId,
      });
      setHasCompletedRun(true);
      setCurrentRunId(runId);
    } catch {
      finalizedRunsRef.current.delete(runId);
      appendDurableMessage({
        id: `run-load-error-${runId}`,
        role: 'error',
        text: 'The workflow finished, but I could not load its result.',
        runId,
      });
    }
  }, [appendDurableMessage, hydrateRun]);

  const showGateOrResult = useCallback(async (runId: string) => {
    try {
      const gate = await api.pendingGate(runId);
      const intervention = meta ? interventionFromPendingGate(gate, meta) : null;
      if (intervention) {
        appendDurableMessage({
          id: `intervention-${runId}-${intervention.nodeId}`,
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
          if (node) ensureActivityMessage(runId, node.id);
          void hydrateRun(runId).then(run => {
            setPausePending(false);
            if (node) {
              setActivities(current => ({
                ...current,
                [activityKey(runId, node.id)]: {
                  nodeId: node.id,
                  nodeType: node.type,
                  displayName: node.displayName,
                  agentRole: node.agentRole,
                  status: 'needs_input',
                  text: run.pause_kind === 'user_requested'
                    ? 'Paused before this step. Resume when you are ready.'
                    : 'I need your input before the workflow can continue.',
                  recoveryActions: node.recoveryActions,
                },
              }));
            }
            if (run.pause_kind !== 'user_requested') void showGateOrResult(runId);
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
        ensureActivityMessage(runId, node.id);
        if (event.type === 'node_started') {
          setActivities(current => ({
            ...current,
            [activityKey(runId, node.id)]: {
              nodeId: node.id,
              nodeType: node.type,
              displayName: node.displayName,
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
  }, [appendAssistantFromRun, ensureActivityMessage, hydrateRun, meta, showGateOrResult]);


  const runWorkflowOnce = useCallback(async (text: string, displayText = text) => {
    if (!yamlText || !meta || !conversationId || runSubmissionRef.current) return;
    runSubmissionRef.current = true;
    setRunDetail(null);
    setSelectedNodeId(null);
    const messageId = newId();
    const requestedRunId = crypto.randomUUID();
    const userMessage: DurableChatMessage = {
      id: messageId, role: 'user', text: displayText.trim() !== '' ? displayText : '(submitted form)',
      runId: requestedRunId,
      ...(attachments.length > 0 ? { attachments } : {}),
    };
    setMessages(current => [...current, userMessage]);
    setRunning(true);
    try {
      await persistMessage(userMessage);
      const inputs = buildRunInputs(meta, text, formValues, attachments);
      const executionYaml = withTransformModel(yamlText, selectedModel);
      const { run_id: runId } = await api.runWorkflow(executionYaml, inputs, {
        origin: 'chat_saved_workflow',
        history_visibility: 'conversation_only',
        run_id: requestedRunId,
        workflow_id: workflowId,
        conversation_id: conversationId,
        message_id: messageId,
      });
      setCurrentRunId(runId);
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
      setComposerMode('auto');
    }
  }, [appendDurableMessage, attachments, conversationId, formValues, hydrateRun, meta, observeAttempt, persistMessage, selectedModel, workflowId, yamlText]);

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
    } catch (error) {
      setAttachmentError(error instanceof Error ? error.message : 'The files could not be uploaded.');
    } finally {
      setUploading(false);
    }
  }, [meta?.allowAttachments]);

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
    setSelectedNodeId(null);
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

  const onInterventionResult = useCallback(async (result: unknown, messageId: string) => {
    const record = (result ?? {}) as Record<string, unknown>;
    const status = typeof record.status === 'string' ? record.status : 'completed';
    const interventionMessage = messages.find(message => message.id === messageId);
    const resolvedMessage = interventionMessage?.role === 'intervention'
      ? { ...interventionMessage, status: 'resolved' as const, resolution: status }
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
    message => message.role === 'intervention' && message.status === 'pending',
  );
  const busy = running || askBusy || uploading;
  const disabledReason = runDetail?.status === 'paused'
    ? runDetail.pause_kind === 'user_requested'
      ? 'Resume this paused attempt before sending another message.'
      : 'Resolve the pending review to continue.'
    : composerDisabledReason(interventionPending, busy);

  // These projections depend on workflow/run metadata, not composer text.
  // Keeping them stable avoids rebuilding model menus and activity maps on
  // every keystroke in this otherwise large conversation component.
  const modelOptions = useMemo(() => {
    if (!meta) return { visibleModels: [], workflowOpenRouterModels: [] };
    const compatibleModels = compatibleTransformModels(meta);
    const hasModelConstraints = meta.nodes.some(node => (
      node.type === 'TransformAgent'
      && node.config.mode !== 'deterministic'
      && node.allowedModels.length > 0
    ));
    const visibleModels = models.filter(model => (
      model.automatic || !hasModelConstraints || compatibleModels.includes(model.name)
    ));
    const workflowOpenRouterModels = [...new Set(meta.nodes.flatMap(node => (
      node.type === 'TransformAgent' && node.selectedModel?.startsWith('openrouter/')
        ? [node.selectedModel]
        : []
    )))].filter(model => !visibleModels.some(item => item.name === model));
    return { visibleModels, workflowOpenRouterModels };
  }, [meta, models]);
  const contextActivities = useMemo(() => (
    currentRunId && meta
      ? Object.fromEntries(meta.nodes.flatMap(node => {
          const activity = activities[activityKey(currentRunId, node.id)];
          return activity ? [[node.id, activity]] : [];
        }))
      : {}
  ), [activities, currentRunId, meta]);
  const selectedNode = useMemo(
    () => meta?.nodes.find(node => node.id === selectedNodeId) ?? null,
    [meta, selectedNodeId],
  );

  const openDeepResearch = useCallback(async (question = '') => {
    setControlError(null);
    try {
      const workflow = await api.ensureDeepResearchChatWorkflow();
      const query = question.trim() ? `?prompt=${encodeURIComponent(question.trim())}` : '';
      navigate(`/chat/private/${encodeURIComponent(workflow.id)}${query}`);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : 'Deep Research is unavailable.');
    }
  }, [navigate]);

  const executeFollowUp = useCallback(async (text: string, output: 'pdf' | 'pptx') => {
    if (!currentRunId) return;
    setAskBusy(true);
    setControlError(null);
    try {
      const prepared = await api.prepareChatWorkspace({
        objective: text,
        preferred_output: output,
        previous_run_id: currentRunId,
      });
      navigate(`/chat/private/${encodeURIComponent(prepared.workflow.id)}?prompt=${encodeURIComponent(text)}`);
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : 'The follow-up workflow could not be prepared.');
    } finally {
      setAskBusy(false);
    }
  }, [currentRunId, navigate]);

  const submit = useCallback((event?: FormEvent) => {
    event?.preventDefault();
    if (disabledReason || !meta) return;
    const text = composerText;
    if (/^\/research(?:\s|$)/i.test(text)) {
      setComposerText('');
      void openDeepResearch(text.replace(/^\/research\s*/i, ''));
      return;
    }
    const effectiveFormat = responseFormat === 'auto' ? classifyResponseFormat(text) : responseFormat;
    const instructedText = `${formatHint(effectiveFormat, writingStyle)}\n\n${text}`;
    setComposerText('');
    const intent = resolveComposerIntent(hasCompletedRun, composerMode);
    const executionOutput = composerMode === 'auto' && hasCompletedRun
      ? followUpExecutionOutput(text)
      : null;
    if (executionOutput) void executeFollowUp(text, executionOutput);
    else if (intent === 'run') void runWorkflowOnce(instructedText, text);
    else void askFollowUp(instructedText, text);
  }, [askFollowUp, composerMode, composerText, disabledReason, executeFollowUp, hasCompletedRun, meta, openDeepResearch, responseFormat, runWorkflowOnce, writingStyle]);

  function chooseSlashCommand(command: SlashCommand) {
    const result = applySlashCommand(command, composerText);
    setComposerText(result.text);
    if (result.format) setResponseFormat(result.format);
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
          ← Back to workflows
        </button>
      </div>
    );
  }

  if (!meta) {
    return <div className="px-6 py-10 text-sm text-ink-500">Loading workflow…</div>;
  }

  const title = meta.chatbotName ?? resourceName;
  const selectedModelInfo = models.find(model => model.name === selectedModel);
  const { visibleModels, workflowOpenRouterModels } = modelOptions;
  const selectedNodeRun: NodeRun | undefined = selectedNodeId
    ? runDetail?.node_runs[selectedNodeId]
    : undefined;

  return (
    <div className="relative flex h-full min-h-0 bg-white">
      <div className="mx-auto flex min-w-0 max-w-4xl flex-1 flex-col px-4 py-4 sm:px-6">
      <header className="flex items-start justify-between gap-4 border-b border-slate-200 pb-3">
        <div>
          <h1 className="text-lg font-semibold text-ink-900">{title}</h1>
          <p className="mt-0.5 text-xs text-ink-500">{source === 'private' ? `🔒 ${resourceName} · Private` : resourceName}</p>
        </div>
        <div className="flex gap-2">
          {!contextOpen && <button type="button" onClick={() => { contextManuallyToggledRef.current = true; setContextOpen(true); }} className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50">Workflow</button>}
          <button type="button" onClick={() => navigate('/chat')} className="rounded-md border border-slate-300 px-3 py-1.5 text-xs text-ink-700 hover:bg-slate-50">← All workflows</button>
        </div>
      </header>

      {currentRunId && (
        <div className="border-b border-slate-100 py-2">
          <WorkflowExecutionStrip meta={meta} activities={contextActivities} selectedNodeId={selectedNodeId} onSelect={nodeId => selectNode(nodeId)} />
        </div>
      )}

      {runDetail && runDetail.run_id === currentRunId && (
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
        {messages.map(message => (
          <MessageView
            key={message.id}
            message={message}
            activities={activities}
            selectedNodeId={selectedNodeId}
            onSelectNode={selectNode}
            onInterventionResult={onInterventionResult}
            onOpenActivity={runId => navigate(`/workflow-runs/${runId}`)}
          />
        ))}
      </div>

      <form onSubmit={submit} className="mt-4 border-t border-slate-200 pt-3">
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <button type="button" onClick={() => setTemplatesOpen(true)} className="rounded-md border border-slate-300 px-2 py-1 text-xs text-ink-700">Templates</button>
          <button type="button" onClick={() => void openDeepResearch(composerText)} className="rounded-md border border-slate-300 px-2 py-1 text-xs text-ink-700">Deep research</button>
          <label className="text-[11px] text-ink-500">Format <select aria-label="Response format" value={responseFormat} onChange={e => setResponseFormat(e.target.value as ResponseFormat)} className="ml-1 rounded border border-slate-300 py-1 text-xs"><option value="auto">Auto</option><option value="prose">Prose</option><option value="bullets">Bullets</option><option value="numbered">Numbered</option><option value="table">Table</option><option value="chart">Chart</option></select></label>
          <label className="text-[11px] text-ink-500">Style <select aria-label="Writing style" value={writingStyle} onChange={e => setWritingStyle(e.target.value as WritingStyle)} className="ml-1 rounded border border-slate-300 py-1 text-xs"><option value="concise">Concise</option><option value="detailed">Detailed</option><option value="academic">Academic</option><option value="casual">Casual</option><option value="executive">Executive</option><option value="bullet-first">Bullet-first</option></select></label>
          <span className="text-[10px] text-ink-400">Auto: {classifyResponseFormat(composerText || 'explain')}</span>
        </div>
        {meta.startMode === 'input_form' && meta.formFields.length > 0 && (
          <div className="mb-3 grid gap-2 sm:grid-cols-2">
            {meta.formFields.map(field => (
              <label key={field.name} className="block text-xs text-ink-700">
                {field.label}
                {field.required && <span className="ml-1 text-bad">*</span>}
                <input
                  type={field.fieldType === 'number' ? 'number' : 'text'}
                  value={formValues[field.name] ?? ''}
                  onChange={e => setFormValues(current => ({
                    ...current, [field.name]: e.target.value,
                  }))}
                  disabled={busy || interventionPending}
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border disabled:bg-slate-50"
                />
              </label>
            ))}
          </div>
        )}
        {hasCompletedRun && (
          <select
            value={composerMode}
            onChange={e => setComposerMode(e.target.value as 'auto' | 'ask' | 'run')}
            className="mb-2 block rounded-md border-slate-300 text-xs py-1 px-2 border"
            disabled={busy || interventionPending}
          >
            <option value="auto">
              {hasCompletedRun ? 'Ask AI about this result' : 'Run workflow'}
            </option>
            <option value="ask">Ask AI about this result</option>
            <option value="run">Run workflow again</option>
          </select>
        )}
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
          <textarea
            value={composerText}
            onChange={e => setComposerText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            rows={2}
            placeholder={
              meta.startMode === 'input_form'
                ? 'Fill the fields above, then send…'
                : 'Ask a question…'
            }
            disabled={Boolean(disabledReason)}
            className="block w-full resize-none border-0 bg-transparent px-2 py-2 text-sm outline-none disabled:bg-slate-50"
          />
          <div className="flex items-center justify-between gap-2 border-t border-slate-100 pt-2">
            <div className="flex flex-wrap items-center gap-1">
              {meta.allowAttachments && <button type="button" onClick={() => fileInputRef.current?.click()} disabled={busy} className="rounded-md px-2 py-1.5 text-xs text-ink-600 hover:bg-slate-100 disabled:opacity-50">+ Attach</button>}
              {meta.capabilities.web && <span title="This workflow can use Web Search" className="rounded-md bg-sky-50 px-2 py-1.5 text-xs text-sky-700">🌐 Web</span>}
              {meta.capabilities.sources && <span title="This workflow retrieves citations from configured knowledge sources" className="rounded-md bg-amber-50 px-2 py-1.5 text-xs text-amber-700">📚 Sources</span>}
              {meta.capabilities.images && <span title="This workflow can create image artifacts" className="rounded-md bg-fuchsia-50 px-2 py-1.5 text-xs text-fuchsia-700">🎨 Images</span>}
              {meta.capabilities.tools && <span title="Tools configured by this workflow" className="rounded-md bg-violet-50 px-2 py-1.5 text-xs text-violet-700">Tools</span>}
              {meta.capabilities.mcp && <span title="MCP resources configured by this workflow" className="rounded-md bg-emerald-50 px-2 py-1.5 text-xs text-emerald-700">🔌 MCP</span>}
              {meta.capabilities.models && (
                <label className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-xs text-ink-600">
                  <span>Model</span>
                  <select
                    aria-label="Model for Transform agents"
                    value={selectedModel}
                    onChange={event => setSelectedModel(event.target.value)}
                    disabled={busy || visibleModels.length === 0}
                    className="max-w-44 border-0 bg-transparent py-0 pl-1 pr-5 text-xs font-medium text-ink-800 outline-none disabled:opacity-50"
                  >
                    <option value="workflow_default">Workflow default</option>
                    {visibleModels.map(model => (
                      <option key={model.name} value={model.name}>
                        {model.display_name}{model.local ? ' · Local' : ''}
                      </option>
                    ))}
                    {workflowOpenRouterModels.map(model => (
                      <option key={model} value={model}>
                        {model.replace(/^openrouter\//, '')} · OpenRouter
                      </option>
                    ))}
                  </select>
                </label>
              )}
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
        </div>
        {attachmentError && <p className="mt-1 text-xs text-bad">{attachmentError}</p>}
        {modelsError && <p className="mt-1 text-xs text-bad">{modelsError}</p>}
        {speechError && <p className="mt-1 text-xs text-bad">{speechError}</p>}
        {transcriptSyncError && (
          <p className="mt-1 text-xs text-bad" role="alert">
            Transcript sync failed: {transcriptSyncError}. New messages may not survive refresh.
          </p>
        )}
        {selectedModel !== 'workflow_default' && (
          <p className="mt-1 text-xs text-ink-500">
            {selectedModelInfo?.display_name ?? selectedModel} will be used by compatible LLM Transform steps for this run only.
          </p>
        )}
        {disabledReason && <p className="mt-1 text-xs text-ink-500">{disabledReason}</p>}
      </form>
      </div>
      {contextOpen && <WorkflowContextPanel meta={meta} activities={contextActivities} attemptLabel={runDetail ? attemptLabel(runDetail.attempt, runDetail.reused_node_count) : undefined} selectedNodeId={selectedNodeId} onSelect={nodeId => selectNode(nodeId)} onClose={() => { contextManuallyToggledRef.current = true; setContextOpen(false); }} />}
      {inspectorOpen && selectedNode && <ChatNodeInspector node={selectedNode} nodeRun={selectedNodeRun} onClose={() => setInspectorOpen(false)} />}
      {templatesOpen && <PromptTemplateLibrary onClose={() => setTemplatesOpen(false)} onInsert={text => setComposerText(text)} />}
    </div>
  );
}


// ---- Message rendering --------------------------------------------------

function MessageView({
  message,
  activities,
  selectedNodeId,
  onSelectNode,
  onInterventionResult,
  onOpenActivity,
}: {
  message: ChatMessage;
  activities: Record<string, AgentActivity>;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
  onInterventionResult: (result: unknown, messageId: string) => void;
  onOpenActivity: (runId: string) => void;
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

  if (message.role === 'activity') {
    const activity = activities[message.activityKey];
    if (!activity) return null;
    return <AgentActivityCard activity={activity} selected={selectedNodeId === message.nodeId} onSelect={() => onSelectNode(message.nodeId)} />;
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
    return (
      <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900 shadow-sm">
        <p>{message.text}</p>
        {message.runId && (
          <button
            type="button"
            onClick={() => onOpenActivity(message.runId as string)}
            className="mt-2 text-xs font-medium underline"
          >
            View workflow activity
          </button>
        )}
      </div>
    );
  }

  if (message.role === 'intervention') {
    return (
      <InterventionCard
        message={message}
        onResult={result => onInterventionResult(result, message.id)}
      />
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[90%] space-y-3 rounded-lg border border-slate-200 bg-white px-4 py-3 shadow-sm">
        {message.segments.map((segment, index) => <SegmentView key={index} segment={segment} />)}
        {message.runId && (
          <button type="button" onClick={() => onOpenActivity(message.runId as string)} className="text-[11px] text-ink-400 hover:underline">
            View workflow activity
          </button>
        )}
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

function SegmentView({ segment }: { segment: AssistantSegment }) {
  if (segment.kind === 'text') {
    const parts = segment.text.split(/(\[\d+\])/g);
    return <p className="whitespace-pre-wrap text-sm text-ink-800">{parts.map((part, index) => {
      const match = /^\[(\d+)\]$/.exec(part);
      if (!match) return part;
      const number = match[1];
      return <button key={`${part}-${index}`} type="button" onClick={() => {
        const target = document.getElementById(`chat-citation-${number}`) as HTMLDetailsElement | null;
        if (target) { target.open = true; target.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
      }} className="mx-0.5 font-medium text-accent-700 hover:underline">{part}</button>;
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
      <div>
        <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">Sources</div>
        <div className="mt-2 grid gap-2">
          {segment.items.map(item => <details id={`chat-citation-${item.number}`} key={`${item.documentId ?? item.title}:${item.chunkId ?? item.number}`} className="scroll-mt-4 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs">
            <summary className="cursor-pointer font-medium text-ink-700"><span className="mr-1 text-accent-700">[{item.number}]</span>{item.title}{item.page ? ` · page ${item.page}` : ''}{item.section ? ` · ${item.section}` : ''}</summary>
            {item.snippet && <blockquote className="mt-2 border-l-2 border-accent-300 pl-2 text-ink-600">{item.snippet}</blockquote>}
            <div className="mt-2 flex items-center gap-3 text-[10px] text-ink-400">
              {item.evidenceStatus === 'retrieved_not_verified' && <span>Retrieved passage · not independently verified</span>}
              {item.evidenceStatus === 'candidate_only' && <span>{item.sourceType === 'research_paper' ? 'Research paper' : 'Web result'} · candidate source</span>}
              {item.evidenceStatus === 'acquired_full_text' && <span>Acquired full text · not independently verified</span>}
              {item.sourceUri && <a href={item.sourceUri} target="_blank" rel="noreferrer" className="text-accent-700 hover:underline">Visit webpage</a>}
              {item.downloadUrl && <a href={`${apiBase()}${item.downloadUrl}`} className="text-accent-700 hover:underline">Download PDF</a>}
            </div>
          </details>)}
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

function InterventionCard({
  message,
  onResult,
}: {
  message: Extract<ChatMessage, { role: 'intervention' }>;
  onResult: (result: unknown) => void;
}) {
  const { request } = message;
  if (message.status === 'resolved') {
    return (
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900 shadow-sm">
        Review resolved{message.resolution ? ` — ${message.resolution}` : ''}.
      </div>
    );
  }
  return (
    <div className="rounded-lg border border-accent-200 bg-white shadow-sm">
      <div className="border-b border-accent-100 px-4 py-2 text-xs font-semibold uppercase tracking-wide text-accent-700">
        Approval required — {request.displayName}
      </div>
      <div className="p-4">
        <HITLPanel
          runId={request.runId}
          pausedNodeId={request.nodeId}
          pausedStepName={request.displayName}
          reviewPurpose={request.reviewPurpose}
          question={request.question}
          context={request.context}
          allowedActions={request.allowedActions}
          content={request.content as never}
          allowDocumentOverride={request.allowDocumentOverride}
          maxEditChars={request.maxEditChars}
          onResult={onResult}
          onSubmitting={() => undefined}
          onSubmitError={() => undefined}
          onClose={() => undefined}
          plainTextJsonEditing
        />
      </div>
    </div>
  );
}

