import type { ExecutionKind } from '../../../api/types';

/**
 * Makes the automation boundary visible (§25).
 *
 * A workflow should show at a glance where a model decides, where code decides,
 * where something leaves the building, and where a person decides. That
 * distinction is the whole reviewability argument for this platform, so it is a
 * badge on the node rather than a detail in the inspector.
 */

export const EXECUTION_KINDS: Record<
  ExecutionKind,
  { label: string; hint: string; className: string; dot: string }
> = {
  ai: {
    label: 'Uses model',
    hint: 'A language model decides this step’s output.',
    className: 'bg-violet-50 text-violet-700 border-violet-200',
    dot: 'bg-violet-500',
  },
  deterministic: {
    label: 'Deterministic',
    hint: 'Code decides this. Same input, same result, no tokens.',
    className: 'bg-emerald-50 text-emerald-700 border-emerald-200',
    dot: 'bg-emerald-500',
  },
  external: {
    label: 'External action',
    hint: 'This step acts on a system outside the platform.',
    className: 'bg-amber-50 text-amber-800 border-amber-200',
    dot: 'bg-amber-500',
  },
  human: {
    label: 'Human decision',
    hint: 'The run pauses here until a person decides.',
    className: 'bg-sky-50 text-sky-700 border-sky-200',
    dot: 'bg-sky-500',
  },
  input: {
    label: 'Input',
    hint: 'Information entering the workflow.',
    className: 'bg-slate-100 text-slate-700 border-slate-200',
    dot: 'bg-slate-400',
  },
  output: {
    label: 'Output',
    hint: 'Produces a result that leaves the workflow.',
    className: 'bg-slate-100 text-slate-700 border-slate-200',
    dot: 'bg-slate-400',
  },
};

export function ExecutionKindBadge({
  kind,
  compact = false,
}: {
  kind: ExecutionKind;
  compact?: boolean;
}) {
  const meta = EXECUTION_KINDS[kind] ?? EXECUTION_KINDS.deterministic;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-1.5 py-0.5 text-[9px] font-medium ${meta.className}`}
      title={meta.hint}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${meta.dot}`} />
      {compact ? null : meta.label}
    </span>
  );
}
