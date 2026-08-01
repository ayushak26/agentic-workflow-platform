// Shared per-node rendering helpers, originally local to RunHistory.tsx and
// extracted here so the Cockpit inspector panel's tabs can reuse the exact
// same status colors, type colors, and output-formatting heuristics rather
// than drifting into a second, slightly-different implementation. Mixes
// components (StatusPill, FileInputValue) with plain constants/functions,
// which is exactly what react-refresh/only-export-components warns about —
// deliberately disabled here since this module is a shared utilities file,
// not a component meant to hot-reload standalone.
/* eslint-disable react-refresh/only-export-components */
import type { NavigateFunction } from 'react-router-dom';
import { api } from '../../../api/client';
import { CopyButton } from '../../../components/CopyButton';
import type { NodeRun, NodeRunStatus, RunDetail, WorkflowFileReference } from '../../../api/types';
import { fileKey } from '../file-artifact';
import type { NodeStatus } from '../cockpit-state';

// Colour-coded status pill — a flat gray label reads the same for every
// outcome, which makes "failed" and "successful" equally easy to miss when
// scanning a long list. Colour does the work uppercase alone can't.
export const STATUS_PILL: Record<string, string> = {
  running: 'bg-blue-50 text-blue-700',
  paused: 'bg-amber-50 text-amber-800',
  completed: 'bg-emerald-50 text-emerald-700',
  reused: 'bg-cyan-50 text-cyan-700',
  rejected: 'bg-amber-50 text-amber-800',
  failed: 'bg-red-50 text-red-700',
  skipped: 'bg-slate-100 text-ink-500',
  cancelled: 'bg-slate-100 text-ink-500',
  pending: 'bg-slate-100 text-ink-500',
};

export function StatusPill({ status, label }: { status: string; label: string }) {
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
export const TYPE_STYLE: Record<string, { dot: string; chip: string; label: string }> = {
  TransformAgent: { dot: 'bg-violet-500', chip: 'bg-violet-50 text-violet-700', label: 'Transform' },
  RAGAgent: { dot: 'bg-teal-500', chip: 'bg-teal-50 text-teal-700', label: 'RAG' },
  MCPAgent: { dot: 'bg-blue-500', chip: 'bg-blue-50 text-blue-700', label: 'MCP' },
  RouterAgent: { dot: 'bg-amber-500', chip: 'bg-amber-50 text-amber-700', label: 'Router' },
  HumanInLoopAgent: { dot: 'bg-pink-500', chip: 'bg-pink-50 text-pink-700', label: 'Human' },
  ExcelToolNode: { dot: 'bg-green-500', chip: 'bg-green-50 text-green-700', label: 'Excel' },
  PowerPointToolNode: { dot: 'bg-orange-500', chip: 'bg-orange-50 text-orange-700', label: 'PowerPoint' },
  PDFToolNode: { dot: 'bg-red-500', chip: 'bg-red-50 text-red-700', label: 'PDF' },
};
export const TYPE_FALLBACK = { dot: 'bg-slate-400', chip: 'bg-slate-100 text-ink-500', label: 'Node' };

export function typeStyle(t: string | undefined) {
  return (t && TYPE_STYLE[t]) || TYPE_FALLBACK;
}

// Shared by Cockpit (live) and Run History (historical) so a node's color
// and label never drift between the two screens.
export const NODE_RUN_STATUS_MAP: Record<NodeRunStatus, NodeStatus> = {
  running: 'active',
  paused: 'paused',
  completed: 'done',
  reused: 'reused',
  failed: 'failed',
};

/**
 * A finished run has no live DAG to walk (unlike Cockpit's
 * computeReachability/applyCancellation over a running graph), so this is
 * a simplified, history-only classification: any node with a recorded
 * NodeRun reports its real status; any node the workflow defines but that
 * never got a NodeRun is only meaningfully "skipped" once the run itself
 * has ended (`isTerminal`) — otherwise it's still just waiting its turn.
 */
export function historicalNodeStatus(
  nodeId: string,
  nodeRunById: Record<string, NodeRun>,
  isTerminal: boolean,
): NodeStatus {
  const run = nodeRunById[nodeId];
  if (run) return NODE_RUN_STATUS_MAP[run.status];
  return isTerminal ? 'skipped' : 'pending';
}

/** Compact duration for the graph node card, e.g. "480ms"/"2.4s". */
export function shortDuration(seconds: number | null | undefined): string | null {
  if (seconds == null) return null;
  return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds.toFixed(1)}s`;
}

export function clock(v: string | number | null): string {
  if (v == null) return '—';
  const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v);
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// --- Output rendering -------------------------------------------------------
export function renderValue(value: unknown): string {
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

export function readableOutput(output: unknown): string {
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

/**
 * Small at-a-glance summary for a node's output card in the graph itself —
 * deliberately NOT the full output (that lives in the inspector's Output
 * tab). Record count for arrays/objects, first line for text.
 */
export function outputSummary(output: unknown): string | null {
  if (output == null) return null;
  if (Array.isArray(output)) return `${output.length} record${output.length === 1 ? '' : 's'}`;
  if (typeof output === 'string') {
    const firstLine = output.split('\n')[0].trim();
    return firstLine.length > 48 ? `${firstLine.slice(0, 48)}…` : firstLine || null;
  }
  if (typeof output === 'object') {
    const obj = output as Record<string, unknown>;
    const text = typeof obj.raw === 'string' ? obj.raw
      : typeof obj.answer === 'string' ? obj.answer
      : typeof obj.summary === 'string' ? obj.summary
      : null;
    if (text) {
      const firstLine = text.split('\n')[0].trim();
      return firstLine.length > 48 ? `${firstLine.slice(0, 48)}…` : firstLine || null;
    }
    const keyCount = Object.keys(obj).length;
    return keyCount > 0 ? `${keyCount} field${keyCount === 1 ? '' : 's'}` : null;
  }
  return String(output);
}

const IMAGE_EXTENSIONS = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp']);

export type ArtifactClassification = {
  key: string;
  extension: string;
  isImage: boolean;
  isFile: boolean;
};

/**
 * Classifies a node output's downloadable artifact (if any) as an image
 * vs. a generic file, shared by the inspector's Output tab and Run
 * History's Outputs tab so both render the same preview/download
 * affordance instead of two slightly different ones.
 */
export function classifyArtifact(output: unknown): ArtifactClassification | null {
  const key = fileKey(output);
  if (!key) return null;
  const extension = key.split('.').pop()?.toLowerCase() ?? '';
  const isImage = IMAGE_EXTENSIONS.has(extension);
  return { key, extension, isImage, isFile: !isImage };
}

export function workflowFileRefs(value: unknown): WorkflowFileReference[] {
  const candidates = Array.isArray(value) ? value : [value];
  return candidates.filter((candidate): candidate is WorkflowFileReference => (
    Boolean(candidate)
    && typeof candidate === 'object'
    && (candidate as Record<string, unknown>).kind === 'workflow_file'
    && typeof (candidate as Record<string, unknown>).minio_key === 'string'
  ));
}

export function FileInputValue({ value }: { value: unknown }) {
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

// A small static lookup, not an LLM-generated suggestion — maps common
// failure substrings to a plain-language next step for a non-technical
// user. Falls back to null (no suggestion shown) rather than guessing.
const SUGGESTION_RULES: Array<{ match: RegExp; suggestion: string }> = [
  { match: /timeout|timed out/i, suggestion: 'The operation took too long. Try again, or check whether the upstream service is slow or unavailable.' },
  { match: /rate.?limit|429/i, suggestion: 'The provider is rate-limiting requests. Wait a moment and retry.' },
  { match: /unauthorized|forbidden|401|403|auth/i, suggestion: 'This looks like an authentication/permission problem. Check the configured credentials for this node.' },
  { match: /not found|404/i, suggestion: 'A referenced resource (file, model, or endpoint) could not be found. Check the node configuration.' },
  { match: /connection|network|econnrefused|dns/i, suggestion: 'A network connection failed. Check connectivity to the dependent service and retry.' },
  { match: /validation|invalid|schema/i, suggestion: 'The input or output didn\'t match the expected shape. Check the node configuration and its inputs.' },
];

export function suggestedCorrectiveAction(error: string | null | undefined): string | null {
  if (!error) return null;
  const rule = SUGGESTION_RULES.find((r) => r.match.test(error));
  return rule?.suggestion ?? null;
}

/**
 * Re-triggers a failed run from its last reusable checkpoint by navigating
 * to a fresh Cockpit run bound to `retrySourceRunId` — the same
 * client-side pattern RunHistory.tsx's own retry button uses (there is no
 * dedicated retry API call; /run_workflow itself reuses completed nodes
 * when given a retrySourceRunId). Returns an error message when retry isn't
 * possible instead of throwing, so callers can just render it.
 */
export function startRetryRun(
  run: Pick<RunDetail, 'run_id' | 'status' | 'retry_available' | 'workflow_yaml' | 'workflow_name'>,
  navigate: NavigateFunction,
): string | null {
  if (run.status !== 'failed') {
    return 'This run has not failed — there is nothing to retry.';
  }
  if (!run.retry_available || !run.workflow_yaml) {
    return (
      'This run predates retry checkpoints. Run the workflow once after '
      + 'installing this update; future failures can resume safely.'
    );
  }
  const retryRunId = crypto.randomUUID();
  navigate(`/cockpit/${retryRunId}`, {
    state: {
      workflowYaml: run.workflow_yaml,
      workflowName: run.workflow_name,
      retrySourceRunId: run.run_id,
    },
  });
  return null;
}
