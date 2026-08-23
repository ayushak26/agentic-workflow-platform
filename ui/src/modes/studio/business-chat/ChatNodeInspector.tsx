import type { NodeRun } from '../../../api/types';
import type { WorkflowChatNode } from './businessChatModel';

function JsonValue({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-[11px] leading-5 text-slate-100">
      {JSON.stringify(value, null, 2) ?? '—'}
    </pre>
  );
}

export function ChatNodeInspector({
  node,
  nodeRun,
  onClose,
}: {
  node: WorkflowChatNode;
  nodeRun?: NodeRun;
  onClose: () => void;
}) {
  return (
    <div className="absolute inset-y-0 right-0 z-30 flex w-full max-w-lg flex-col border-l border-slate-200 bg-white shadow-2xl">
      <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-accent-700">Node details</p>
          <h2 className="mt-1 text-base font-semibold text-ink-900">{node.displayName}</h2>
          <p className="text-xs text-ink-400">{node.id} · {node.type}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close node details" className="rounded-md px-2 py-1 text-ink-500 hover:bg-slate-100">×</button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-5 text-sm">
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">Overview</h3>
          <dl className="mt-2 grid grid-cols-2 gap-3 rounded-lg border border-slate-200 p-3 text-xs">
            <div><dt className="text-ink-400">Status</dt><dd className="mt-0.5 capitalize text-ink-800">{nodeRun?.status ?? 'waiting'}</dd></div>
            <div><dt className="text-ink-400">Duration</dt><dd className="mt-0.5 text-ink-800">{nodeRun?.duration_s == null ? '—' : `${nodeRun.duration_s.toFixed(2)}s`}</dd></div>
            <div><dt className="text-ink-400">Agent</dt><dd className="mt-0.5 text-ink-800">{node.agentRole ?? node.displayName}</dd></div>
            <div><dt className="text-ink-400">Attempts</dt><dd className="mt-0.5 text-ink-800">1</dd></div>
          </dl>
          {node.purpose && <p className="mt-2 text-xs leading-5 text-ink-600">{node.purpose}</p>}
        </section>
        <details open>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Input</summary>
          <div className="mt-2"><JsonValue value={nodeRun?.input ?? {}} /></div>
        </details>
        <details open>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Output</summary>
          <div className="mt-2"><JsonValue value={nodeRun?.output ?? null} /></div>
        </details>
        {nodeRun?.model_selections && nodeRun.model_selections.length > 0 && (
          <details>
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Model & routing</summary>
            <div className="mt-2"><JsonValue value={nodeRun.model_selections} /></div>
          </details>
        )}
        {nodeRun?.error && (
          <details open>
            <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-bad">Error</summary>
            <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800">{nodeRun.error}</div>
          </details>
        )}
        <details>
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Configuration</summary>
          <div className="mt-2"><JsonValue value={node.config} /></div>
          <p className="mt-2 text-xs leading-5 text-ink-500">This is the configuration saved with the workflow execution. Open Builder to edit prompts, models, routing, schemas, guardrails, timeouts, and retries.</p>
        </details>
      </div>
    </div>
  );
}