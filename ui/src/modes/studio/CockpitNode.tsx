import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import type { ModelSelection } from '../../api/types';
import type { WorkflowNodeData } from './yaml-bridge';
import { STATUS_LABEL, type NodeStatus } from './cockpit-state';

export type CockpitNodeData = WorkflowNodeData & {
  status: NodeStatus;
  modelSelection?: ModelSelection;
  // Short duration string (e.g. "2.4s"), already formatted upstream so this
  // component stays a pure renderer with no time-formatting logic of its own.
  durationLabel?: string | null;
  // Small at-a-glance summary — record count or a truncated first line.
  // Never the full output; that lives in the inspector's Output tab.
  outputSummary?: string | null;
  hasError?: boolean;
  // Selection/path-focus/filter visual states — opacity/ring only, so the
  // node's footprint never changes size (positions stay fixed).
  faded?: boolean;
  pathHighlighted?: boolean;
};

// Text glyph alongside the color — status must never be color-only.
// 'active' uses the dedicated running/focus colour (cyan), not the brand
// teal accent — the design system keeps "primary action" and "currently
// running" as distinct roles even though both read as a similar hue.
const STATUS_STYLES: Record<NodeStatus, { border: string; pill: string; glyph: string }> = {
  pending:   { border: 'border-slate-200',                 pill: 'bg-slate-100 text-ink-500',  glyph: '⋯' },
  active:    { border: 'border-running',                   pill: 'bg-running text-white',       glyph: '▶' },
  done:      { border: 'border-ok',                        pill: 'bg-ok text-white',            glyph: '✓' },
  reused:    { border: 'border-cyan-500',                  pill: 'bg-cyan-500 text-white',      glyph: '↺' },
  paused:    { border: 'border-warn',                      pill: 'bg-warn text-white',          glyph: '⏸' },
  failed:    { border: 'border-bad',                       pill: 'bg-bad text-white',           glyph: '✕' },
  skipped:   { border: 'border-skipped border-dashed',     pill: 'bg-skipped/10 text-skipped',  glyph: '↷' },
  cancelled: { border: 'border-cancelled border-dashed',   pill: 'bg-cancelled/10 text-cancelled', glyph: '⊘' },
};

function CockpitNodeImpl({ data, selected }: NodeProps<CockpitNodeData>) {
  const s = STATUS_STYLES[data.status];
  const active = data.status === 'active';
  const faded = data.faded && !selected && !data.pathHighlighted;
  return (
    <div
      className={`bg-white rounded-md border-2 px-3 py-2.5 w-[240px] transition-opacity duration-200 ${s.border} ${
        active ? 'shadow-lg ring-4 ring-running/20' : 'shadow-sm'
      } ${selected || data.pathHighlighted ? 'ring-2 ring-accent-500' : ''} ${
        faded ? 'opacity-30' : 'opacity-100'
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />
      <div className="flex items-center justify-between gap-2">
        <div className="text-[10px] uppercase tracking-wide text-ink-500 truncate">{data.typeName}</div>
        <span
          className={`flex-none inline-flex items-center gap-1 text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 ${s.pill} ${
            active ? 'animate-pulse' : ''
          }`}
        >
          <span aria-hidden="true">{s.glyph}</span>
          {STATUS_LABEL[data.status]}
        </span>
      </div>
      <div className="font-medium text-ink-900 mt-1 truncate" title={data.nodeId}>{data.nodeId}</div>

      <div className="mt-1.5 flex items-center gap-2 min-h-[16px]">
        {data.durationLabel && (
          <span className="text-[11px] text-ink-500 flex-none">{data.durationLabel}</span>
        )}
        {data.outputSummary && (
          <span className="text-[11px] text-ink-500 truncate" title={data.outputSummary}>
            {data.outputSummary}
          </span>
        )}
        {data.hasError && (
          <span
            className="flex-none ml-auto text-bad text-xs font-bold"
            title="This node failed — see the Errors tab"
            aria-label="Error"
          >
            &#9888;
          </span>
        )}
      </div>

      {data.modelSelection && (
        <div className="mt-1.5 text-[10px] text-accent-700 truncate">
          {data.modelSelection.actual_model}
          {data.modelSelection.fallback ? ' · fallback' : ''}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
}

// Positions never change during a run and most ticks only touch one node's
// status — memoizing means the ~N-1 unaffected nodes skip re-render
// entirely as long as the parent only replaces the changed node's object
// (see the status-merge effect in Cockpit.tsx).
export const CockpitNode = memo(CockpitNodeImpl);
