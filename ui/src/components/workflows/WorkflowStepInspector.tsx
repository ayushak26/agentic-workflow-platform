import { WORKFLOW_STATUS_LABEL, type WorkflowStatus } from './workflowStatus';

function JsonValue({ value }: { value: unknown }) {
  return (
    <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-slate-950 p-3 text-[11px] leading-5 text-slate-100">
      {JSON.stringify(value, null, 2) ?? '—'}
    </pre>
  );
}

export function WorkflowStepInspector({
  step,
  onClose,
}: {
  step: {
    id: string;
    name: string;
    type: string;
    status: WorkflowStatus;
    purpose?: string | null;
    input?: unknown;
    output?: unknown;
    instructions?: unknown;
    toolCalls?: unknown;
    intermediateOutput?: unknown;
    error?: string | null;
    durationSeconds?: number | null;
    attempt?: number;
    metadata?: unknown;
  };
  onClose: () => void;
}) {
  return (
    <div className="absolute inset-y-0 right-0 z-30 flex w-full max-w-lg flex-col border-l border-slate-200 bg-white shadow-2xl">
      <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
        <div>
          <p className="text-[11px] font-medium uppercase tracking-wide text-accent-700">Step details</p>
          <h2 className="mt-1 text-base font-semibold text-ink-900">{step.name}</h2>
          <p className="text-xs text-ink-400">{step.id} · {step.type}</p>
        </div>
        <button type="button" onClick={onClose} aria-label="Close step details" className="rounded-md px-2 py-1 text-ink-500 hover:bg-slate-100">×</button>
      </div>
      <div className="flex-1 space-y-5 overflow-y-auto p-5 text-sm">
        <section>
          <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-500">Overview</h3>
          <dl className="mt-2 grid grid-cols-2 gap-3 rounded-lg border border-slate-200 p-3 text-xs">
            <div><dt className="text-ink-400">Status</dt><dd className="mt-0.5 text-ink-800">{WORKFLOW_STATUS_LABEL[step.status]}</dd></div>
            <div><dt className="text-ink-400">Duration</dt><dd className="mt-0.5 text-ink-800">{step.durationSeconds == null ? '—' : `${step.durationSeconds.toFixed(2)}s`}</dd></div>
            <div><dt className="text-ink-400">Step type</dt><dd className="mt-0.5 text-ink-800">{step.type}</dd></div>
            <div><dt className="text-ink-400">Attempt</dt><dd className="mt-0.5 text-ink-800">{step.attempt ?? 1}</dd></div>
          </dl>
          {step.purpose && <p className="mt-2 text-xs leading-5 text-ink-600">{step.purpose}</p>}
        </section>
        <details open><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Input</summary><div className="mt-2"><JsonValue value={step.input ?? {}} /></div></details>
        {step.instructions !== undefined && <details><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Instructions</summary><div className="mt-2"><JsonValue value={step.instructions} /></div></details>}
        {step.toolCalls !== undefined && <details><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Tool calls</summary><div className="mt-2"><JsonValue value={step.toolCalls} /></div></details>}
        {step.intermediateOutput !== undefined && <details><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Intermediate output</summary><div className="mt-2"><JsonValue value={step.intermediateOutput} /></div></details>}
        <details open><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Output</summary><div className="mt-2"><JsonValue value={step.output ?? null} /></div></details>
        {step.error && <details open><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-bad">Error</summary><div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-3 text-xs text-red-800">{step.error}</div></details>}
        {step.metadata !== undefined && <details><summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-ink-500">Execution metadata</summary><div className="mt-2"><JsonValue value={step.metadata} /></div></details>}
      </div>
    </div>
  );
}