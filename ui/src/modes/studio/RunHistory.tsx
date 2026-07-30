import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api,rehydrate } from '../../api/client';
import { CopyButton } from '../../components/CopyButton';
import { artifactLabel, fileKey } from './file-artifact';
import type {
  AuditEvent,
  EventType,
  NodeRun,
  RunDetail,
  RunSummary,
  WorkflowFileReference,
} from '../../api/types';

const STATUS_LABEL: Record<string, string> = {
  running: 'Running',
  paused: 'Paused',
  completed: 'Successful',
  rejected: 'Rejected',
  failed: 'Failed',
};
const STATUS_DOT: Record<string, string> = {
  running: 'bg-blue-500 animate-pulse',
  paused: 'bg-amber-500',
  completed: 'bg-emerald-500',
  rejected: 'bg-amber-500',
  failed: 'bg-red-500',
};
// Colour-coded status pill — a flat gray label reads the same for every
// outcome, which makes "failed" and "successful" equally easy to miss when
// scanning a long run list. Colour does the work uppercase alone can't.
const STATUS_PILL: Record<string, string> = {
  running: 'bg-blue-50 text-blue-700',
  paused: 'bg-amber-50 text-amber-800',
  completed: 'bg-emerald-50 text-emerald-700',
  reused: 'bg-cyan-50 text-cyan-700',
  rejected: 'bg-amber-50 text-amber-800',
  failed: 'bg-red-50 text-red-700',
};

function StatusPill({ status, label }: { status: string; label: string }) {
  return (
    <span
      className={`inline-block text-[10px] font-semibold uppercase tracking-wide px-1.5 py-0.5 rounded ${
        STATUS_PILL[status] ?? 'bg-slate-100 text-ink-700'
      }`}
    >
      {label}
    </span>
  );
}

// Node-type colour coding. Each agent type gets a tint + dot + label.
// Falls back to neutral gray when the type is unknown (e.g. resume-path runs).
const TYPE_STYLE: Record<string, { dot: string; chip: string; label: string }> = {
  TransformAgent: { dot: 'bg-violet-500', chip: 'bg-violet-50 text-violet-700', label: 'Transform' },
  RAGAgent: { dot: 'bg-teal-500', chip: 'bg-teal-50 text-teal-700', label: 'RAG' },
  MCPAgent: { dot: 'bg-blue-500', chip: 'bg-blue-50 text-blue-700', label: 'MCP' },
  RouterAgent: { dot: 'bg-amber-500', chip: 'bg-amber-50 text-amber-700', label: 'Router' },
  HumanInLoopAgent: { dot: 'bg-pink-500', chip: 'bg-pink-50 text-pink-700', label: 'Human' },
  ExcelToolNode: { dot: 'bg-green-500', chip: 'bg-green-50 text-green-700', label: 'Excel' },
  PowerPointToolNode: { dot: 'bg-orange-500', chip: 'bg-orange-50 text-orange-700', label: 'PowerPoint' },
  PDFToolNode: { dot: 'bg-red-500', chip: 'bg-red-50 text-red-700', label: 'PDF' },
};
const TYPE_FALLBACK = { dot: 'bg-slate-400', chip: 'bg-slate-100 text-ink-500', label: 'Node' };

function typeStyle(t: string | undefined) {
  return (t && TYPE_STYLE[t]) || TYPE_FALLBACK;
}

const EVENT_META: Record<EventType, { label: string; dot: string; human: boolean }> = {
  node_start: { label: 'Node started', dot: 'bg-slate-400', human: false },
  node_end: { label: 'Node completed', dot: 'bg-emerald-500', human: false },
  node_reused: { label: 'Node reused (zero tokens)', dot: 'bg-cyan-500', human: false },
  node_error: { label: 'Node error', dot: 'bg-red-500', human: false },
  hitl_approve: { label: 'Approved', dot: 'bg-pink-500', human: true },
  hitl_reject: { label: 'Rejected', dot: 'bg-red-500', human: true },
  hitl_edit: { label: 'Edited', dot: 'bg-pink-500', human: true },
};

function clock(v: string | number | null): string {
  if (v == null) return '—';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// --- Output rendering -------------------------------------------------------
function renderValue(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') {
    const t = value.trim();
    if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
      try { return JSON.stringify(JSON.parse(t), null, 2); } catch { return value; }
    }
    return value;
  }
  return JSON.stringify(value, null, 2);
}

function readableOutput(output: unknown): string {
  if (output == null) return '—';
  if (typeof output === 'string') return renderValue(output);
  if (typeof output === 'object') {
    const obj = output as Record<string, unknown>;
    for (const key of ['raw', 'answer', 'text', 'content', 'result', 'summary']) {
      if (key in obj) return renderValue(obj[key]);
    }
    return JSON.stringify(obj, null, 2);
  }
  return String(output);
}

function workflowFileRefs(value: unknown): WorkflowFileReference[] {
  const candidates = Array.isArray(value) ? value : [value];
  return candidates.filter((candidate): candidate is WorkflowFileReference => (
    Boolean(candidate)
    && typeof candidate === 'object'
    && (candidate as Record<string, unknown>).kind === 'workflow_file'
    && typeof (candidate as Record<string, unknown>).minio_key === 'string'
  ));
}

function FileInputValue({ value }: { value: unknown }) {
  const refs = workflowFileRefs(value);
  if (refs.length === 0) {
    const rendered = renderValue(value);
    return (
      <div className="relative">
        <div className="absolute right-0 top-0">
          <CopyButton text={rendered} />
        </div>
        <pre className="font-mono text-[11px] text-ink-700 whitespace-pre-wrap break-words max-h-64 overflow-y-auto pr-16">
          {rendered}
        </pre>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {refs.map(ref => (
        <div
          key={ref.minio_key}
          className="flex items-center justify-between rounded-md bg-slate-50 px-3 py-2"
        >
          <div className="min-w-0">
            <div className="truncate text-xs font-medium text-ink-700">
              {ref.name}
            </div>
            <div className="text-[10px] text-ink-500">
              {ref.category} · {(ref.size_bytes / 1024).toFixed(1)} KB
              {ref.parseable_text ? ' · text extractable' : ''}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              api.downloadWorkflowFile(ref).catch(error => {
                window.alert(`Download failed: ${String(error)}`);
              });
            }}
            className="ml-3 text-xs text-accent-600 hover:underline"
          >
            Download
          </button>
        </div>
      ))}
    </div>
  );
}

// --- Node card --------------------------------------------------------------
function NodeCard({
  nodeId, typeName, value, nodeRun, open, onToggle,
}: {
  nodeId: string;
  typeName: string | undefined;
  value: unknown;
  nodeRun?: NodeRun;
  open: boolean;
  onToggle: () => void;
}) {
  const ts = typeStyle(typeName);
  const key = fileKey(value);
  const status = nodeRun?.status ?? 'completed';
  const modelSelections = nodeRun?.model_selections ?? [];
  const lastModelSelection = modelSelections.at(-1);
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      {/* A <div role="button">, not a <button> — it wraps the per-node
          Download <button>, and nested <button>s are invalid HTML (React
          warns of a hydration mismatch and some browsers mis-handle the
          click). Keyboard behaviour is preserved via tabIndex + onKeyDown. */}
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToggle(); }
        }}
        className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-slate-50 transition-colors cursor-pointer"
      >
        <div className="flex items-center gap-2.5 min-w-0">
          <span className={`h-2.5 w-2.5 rounded-full flex-none ${ts.dot}`} />
          <span className="font-mono text-sm text-ink-900 truncate">{nodeId}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded flex-none ${ts.chip}`}>{ts.label}</span>
          <StatusPill status={status} label={status} />
          {lastModelSelection && (
            <span className="text-[10px] rounded bg-accent-50 px-1.5 py-0.5 text-accent-700">
              {lastModelSelection.actual_model}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 flex-none ml-3">
          {key && (
            <button
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                void api.downloadArtifact(key);
              }}
              className="text-xs text-accent-600 hover:underline whitespace-nowrap"
              title={key.split('/').pop()}
            >
              Download <span className="text-ink-500">({artifactLabel(value, key)})</span>
            </button>
          )}
          <span className="text-ink-500 text-xs">{open ? 'Hide' : 'View'}</span>
        </div>
      </div>
      {open && (
        <div className="border-t border-slate-100 bg-slate-50 px-4 py-3 space-y-4">
          {nodeRun?.error && (
            <div className="rounded border border-red-200 bg-red-50 p-2 text-xs text-red-700 font-mono">
              {nodeRun.error}
            </div>
          )}
          {modelSelections.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">
                LLM selection
              </div>
              <div className="space-y-2">
                {modelSelections.map((selection, index) => (
                  <div
                    key={`${selection.call_id}:${selection.actual_model}:${index}`}
                    className="rounded border border-accent-200 bg-white p-2 text-xs"
                  >
                    <div className="font-semibold text-accent-800">
                      {selection.actual_model}
                      {selection.fallback ? ' · fallback' : ''}
                      {selection.cache_hit ? ' · cache hit' : ''}
                    </div>
                    <div className="mt-1 text-ink-500">
                      Requested {selection.requested_model} · {selection.mode}
                      {' · '}{selection.complexity}{' '}
                      {selection.task_kind.replace('_', ' ')}
                    </div>
                    <div className="mt-1 text-ink-700">
                      {selection.reason}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-[11px] uppercase tracking-wide text-ink-500">
                Node input
              </div>
              {nodeRun && (
                <CopyButton text={renderValue(nodeRun.input)} label="Copy input" />
              )}
            </div>
            <pre className="text-[12px] leading-relaxed text-ink-700 whitespace-pre-wrap break-words font-mono max-h-72 overflow-y-auto">
              {nodeRun ? renderValue(nodeRun.input) : 'Input not recorded for this older run.'}
            </pre>
          </div>
          <div>
            <div className="flex items-center justify-between mb-1">
              <div className="text-[11px] uppercase tracking-wide text-ink-500">
                Node output
              </div>
              {value != null && (
                <CopyButton text={readableOutput(value)} label="Copy output" />
              )}
            </div>
            <pre className="text-[12px] leading-relaxed text-ink-700 whitespace-pre-wrap break-words font-mono max-h-96 overflow-y-auto">
              {value == null && status === 'running' ? 'Running…' : readableOutput(value)}
            </pre>
            {value != null && typeof value === 'object' && !Array.isArray(value) && (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {Object.entries(value as Record<string, unknown>).map(([field, fieldValue]) => (
                  <CopyButton
                    key={field}
                    text={renderValue(fieldValue)}
                    label={`Copy "${field}" field`}
                    className="text-[9px]"
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function RunHistory() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();

  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [detail, setDetail] = useState<{ run: RunDetail; audit: AuditEvent[] } | null>(null);
  const [listErr, setListErr] = useState<string | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [retryErr, setRetryErr] = useState<string | null>(null);
  const [openNode, setOpenNode] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const load = () => {
      api.runHistory()
        .then((data) => {
          if (cancelled) return;
          setRuns(data.runs);
          setListErr(null);
        })
        .catch(async (error) => {
          if (cancelled) return;
          const msg = String(error);
          // On auth failure, stop polling and try to recover the session from
          // the cookie once. If that fails, surface it instead of hammering.
          if (msg.includes('401')) {
            if (timer) window.clearInterval(timer);
            const user = await rehydrate();
            if (!cancelled && user) {
              load(); // session recovered — resume
              timer = window.setInterval(load, 2500);
            } else if (!cancelled) {
              setListErr('Session expired — please log in again.');
            }
            return;
          }
          setListErr(msg);
        });
    };

    load();
    timer = window.setInterval(load, 2500);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, []);

  useEffect(() => {
    if (!runId && runs.length > 0) navigate(`/history/${runs[0].run_id}`, { replace: true });
  }, [runId, runs, navigate]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    // Clear the previous route's detail before synchronizing the new run.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null);
    setDetailErr(null);
    setRetryErr(null);
    setOpenNode(null);
    const load = () => {
      api.runDetail(runId)
        .then((data) => {
          if (!cancelled) {
            setDetail(data);
            setDetailErr(null);
          }
        })
        .catch((error) => {
          if (!cancelled) setDetailErr(String(error));
        });
    };
    load();
    const timer = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [runId]);

  const outputs = detail ? (detail.run.outputs as Record<string, unknown>) : {};
  const inputs = detail ? (detail.run.inputs as Record<string, unknown>) : {};
  const nodeTypes = detail ? (detail.run.node_types ?? {}) : {};
  const nodeRuns = detail ? (detail.run.node_runs ?? {}) : {};
  const nodeRunById = Object.fromEntries(
    Object.values(nodeRuns).map((record) => [record.node_id, record]),
  ) as Record<string, NodeRun>;
  const nodeIds = Array.from(
    new Set([
      ...Object.keys(nodeRunById),
      ...Object.keys(outputs),
    ]),
  );

  // Everything needed to feed this run into a *different* workflow's inputs:
  // the run's own top-level inputs plus every node's output, flattened and
  // keyed by node id so it matches the {inputName: value} shape RunDialog's
  // "Import inputs from JSON" already expects. Nodes that never produced
  // output (not yet run, or failed before returning anything) are omitted —
  // they'd contribute nothing but a misleading `null`.
  function buildReusableInputsJson(): string {
    const nodeOutputs = Object.fromEntries(
      nodeIds
        .map((id) => [id, nodeRunById[id]?.output ?? outputs[id]])
        .filter(([, value]) => value != null),
    );
    return JSON.stringify({ ...inputs, ...nodeOutputs }, null, 2);
  }

  function retryFailedRun() {
    if (!detail || detail.run.status !== 'failed') return;
    if (!detail.run.retry_available || !detail.run.workflow_yaml) {
      setRetryErr(
        'This run predates retry checkpoints. Run the workflow once after '
        + 'installing this update; future failures can resume safely.',
      );
      return;
    }

    const retryRunId = crypto.randomUUID();
    navigate(`/cockpit/${retryRunId}`, {
      state: {
        workflowYaml: detail.run.workflow_yaml,
        workflowName: detail.run.workflow_name,
        retrySourceRunId: detail.run.run_id,
      },
    });
  }

  return (
    <div className="h-full flex">
      <aside className="flex-none w-64 border-r border-slate-200 overflow-y-auto bg-white">
        {listErr && (
          <div className="m-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            Couldn't load runs. {listErr}
          </div>
        )}
        {!listErr && runs.length === 0 && (
          <div className="p-6 text-center text-ink-500 text-sm">No runs recorded yet.</div>
        )}
        {runs.map((r) => {
          const on = r.run_id === runId;
          return (
            <button
              key={r.run_id}
              onClick={() => navigate(`/history/${r.run_id}`)}
              className={`w-full text-left px-3.5 py-3 border-b border-slate-100 transition-colors ${on ? 'bg-slate-100 border-l-2 border-l-accent-600' : 'border-l-2 border-l-transparent hover:bg-slate-50'
                }`}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm text-ink-900 font-medium truncate">{r.workflow_name}</span>
                <span className={`h-2 w-2 rounded-full flex-none ${STATUS_DOT[r.status] ?? 'bg-slate-300'}`} />
              </div>
              <div className="font-mono text-[11px] text-ink-500 mt-1 truncate">{r.run_id}</div>
              <div className="text-[11px] text-ink-500 mt-0.5">
                {clock(r.started_at ?? r.created_at)} · {r.completed_node_count ?? 0}/{r.node_count ?? 0} nodes
              </div>
              <div className="mt-1.5">
                <StatusPill status={r.status} label={STATUS_LABEL[r.status] ?? r.status} />
              </div>
            </button>
          );
        })}
      </aside>

      <section className="flex-1 min-w-0 overflow-y-auto p-6">
        {detailErr && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {detailErr.includes('404') ? 'Run not found.' : `Couldn't load this run. ${detailErr}`}
          </div>
        )}
        {!detail && !detailErr && <div className="text-ink-500 text-sm">Select a run to view its detail.</div>}

        {detail && (
          <>
            <div className="flex items-baseline justify-between gap-3 flex-wrap">
              <div>
                <div className="text-lg font-medium text-ink-900">{detail.run.workflow_name}</div>
                <div className="font-mono text-xs text-ink-500 mt-0.5">
                  {detail.run.run_id} · started {clock(detail.run.started_at ?? detail.run.created_at)}
                </div>
                {(detail.run.attempt ?? 1) > 1 && (
                  <div className="text-xs text-cyan-700 mt-1">
                    Attempt {detail.run.attempt} · retry of{' '}
                    <button
                      onClick={() => navigate(`/history/${detail.run.retry_of_run_id}`)}
                      className="font-mono hover:underline"
                    >
                      {detail.run.retry_of_run_id}
                    </button>
                  </div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => navigate(`/proposal-review/${detail.run.run_id}`)}
                  className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50"
                >
                  Open proposal review
                </button>
                {nodeIds.length > 0 && (
                  <CopyButton
                    text={buildReusableInputsJson()}
                    label="Copy run as workflow inputs"
                    copiedLabel="Copied"
                  />
                )}
                {detail.run.status === 'failed' && (
                  <button
                    onClick={retryFailedRun}
                    className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500"
                  >
                    Retry from failure
                  </button>
                )}
              </div>
            </div>

            <div className="grid grid-cols-4 gap-2.5 my-5">
              <div className="bg-slate-50 rounded-lg px-3 py-2.5">
                <div className="text-[11px] text-ink-500 mb-1.5">Status</div>
                <StatusPill
                  status={detail.run.status}
                  label={STATUS_LABEL[detail.run.status] ?? detail.run.status}
                />
              </div>
              {[
                { l: 'Duration', v: detail.run.duration_s != null ? `${detail.run.duration_s.toFixed(1)}s` : '—' },
                {
                  l: 'Nodes',
                  v: `${detail.run.completed_node_count ?? 0}/${detail.run.node_count ?? '—'}`
                    + (
                      (detail.run.reused_node_count ?? 0) > 0
                        ? ` · ${detail.run.reused_node_count} reused`
                        : ''
                    ),
                },
                { l: 'Events', v: String(detail.audit.length) },
              ].map((m) => (
                <div key={m.l} className="bg-slate-50 rounded-lg px-3 py-2.5">
                  <div className="text-[11px] text-ink-500 mb-1">{m.l}</div>
                  <div className="text-sm font-medium text-ink-900">{m.v}</div>
                </div>
              ))}
            </div>

            {detail.run.error && (
              <div className="mb-5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 font-mono">
                {detail.run.error}
              </div>
            )}

            {detail.run.status === 'failed' && (
              <div className="mb-5 rounded-md border border-cyan-200 bg-cyan-50 px-3 py-2 text-xs text-cyan-800">
                {detail.run.retry_available ? (
                  <>
                    Retry will reuse {detail.run.retryable_node_count ?? 0} completed
                    {' '}node{detail.run.retryable_node_count === 1 ? '' : 's'} without
                    calling the LLM provider again. The failed and unfinished nodes
                    will run normally.
                  </>
                ) : (
                  <>
                    A reusable checkpoint is not available for this older run.
                    Future runs created after this update will support token-saving retry.
                  </>
                )}
              </div>
            )}

            {retryErr && (
              <div className="mb-5 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                {retryErr}
              </div>
            )}

            {detail.run.active_nodes?.length > 0 && (
              <div className="mb-5 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-xs text-blue-700">
                Running now:{' '}
                <span className="font-mono font-medium">
                  {detail.run.active_nodes.join(', ')}
                </span>
              </div>
            )}

            <div className="mb-5">
              <div className="flex items-center justify-between mb-2">
                <div className="text-xs font-medium text-ink-500">Inputs</div>
                {Object.entries(inputs).length > 0 && (
                  <CopyButton
                    text={JSON.stringify(inputs, null, 2)}
                    label="Copy as JSON"
                    copiedLabel="Copied"
                  />
                )}
              </div>
              <div className="border border-slate-200 rounded-lg divide-y divide-slate-100">
                {Object.entries(inputs).length === 0 ? (
                  <div className="px-4 py-2.5 text-xs text-ink-500">—</div>
                ) : (
                  Object.entries(inputs).map(([k, v]) => (
                    <div key={k} className="px-4 py-2.5">
                      <div className="text-xs text-ink-500 mb-1">{k}</div>
                      <FileInputValue value={v} />
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="mb-5">
              <div className="text-xs font-medium text-ink-500 mb-2">
                Node outputs <span className="text-ink-500 font-normal">· colour = agent type · click to view</span>
              </div>
              <div className="space-y-2">
                {nodeIds.length === 0 ? (
                  <div className="text-xs text-ink-500">
                    No nodes have started yet.
                  </div>
                ) : (
                  nodeIds.map((nodeId) => {
                    const nodeRun = nodeRunById[nodeId];
                    return (
                      <NodeCard
                        key={nodeId}
                        nodeId={nodeId}
                        typeName={nodeRun?.type_name ?? nodeTypes[nodeId]}
                        value={nodeRun?.output ?? outputs[nodeId]}
                        nodeRun={nodeRun}
                        open={openNode === nodeId}
                        onToggle={() => setOpenNode(openNode === nodeId ? null : nodeId)}
                      />
                    );
                  })
                )}
              </div>
            </div>

            <div className="text-xs font-medium text-ink-500 mb-2 flex items-center gap-2">
              Audit trail
              <span className="text-[11px] text-ink-500 font-normal">· highlighted rows are human decisions</span>
            </div>
            <div className="border border-slate-200 rounded-lg divide-y divide-slate-100 overflow-hidden">
              {detail.audit.length === 0 ? (
                <div className="px-4 py-2.5 text-xs text-ink-500">No events recorded.</div>
              ) : (
                detail.audit.map((a, i) => {
                  const e = EVENT_META[a.event_type];
                  const node = a.node_id && a.node_id !== 'unknown' ? a.node_id : null;
                  const reason = (a.payload && (a.payload as Record<string, unknown>).reason) as string | undefined;
                  return (
                    <div
                      key={`${a.node_id}-${a.ts}-${i}`}
                      className={`grid grid-cols-[64px_170px_1fr] gap-3 items-start px-3 py-2 ${e.human ? 'border-l-2 border-l-pink-500 bg-pink-50' : 'border-l-2 border-l-transparent'
                        }`}
                    >
                      <span className="font-mono text-[11px] text-ink-500">{clock(a.ts)}</span>
                      <span className="flex items-center gap-2 text-xs">
                        <span className={`h-1.5 w-1.5 rounded-full ${e.dot}`} />
                        <span className={e.human ? 'text-ink-900 font-medium' : 'text-ink-500'}>{e.label}</span>
                        {node && <span className="font-mono text-[11px] text-ink-500 truncate">{node}</span>}
                      </span>
                      <span className="text-[11px]">
                        <span className={a.actor === 'system' ? 'text-ink-500' : 'text-accent-600 font-medium'}>
                          {a.actor}
                        </span>
                        {reason && <span className="text-ink-500"> — {reason}</span>}
                      </span>
                    </div>
                  );
                })
              )}
            </div>

            <p className="text-[11px] text-ink-500 mt-6">
              Audit payloads record shape only — never prompt or proposal content. Records are append-only and scoped to
              your session.
            </p>
          </>
        )}
      </section>
    </div>
  );
}
