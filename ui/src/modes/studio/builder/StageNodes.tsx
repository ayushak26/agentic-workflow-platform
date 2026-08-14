import { Handle, Position, type NodeProps } from 'reactflow';
import type { BuilderStageBandData, BuilderStagePlaceholderData } from './stage-view';

/**
 * The two things the Builder canvas draws that are not workflow steps: the
 * background band marking a stage's column, and the box that stands in for a
 * collapsed stage.
 *
 * The band is deliberately click-through except for its header control —
 * it covers a large area of empty canvas, and swallowing clicks there would
 * break panning and deselection.
 */

export function BuilderStageBandNode({ data }: NodeProps<BuilderStageBandData>) {
  return (
    <div
      className="pointer-events-none rounded-xl border border-dashed border-slate-200 bg-slate-50/70"
      style={{ width: data.width, height: data.height }}
    >
      <div className="flex items-center gap-2 px-3 py-1.5">
        <span className="text-[10px] font-medium uppercase tracking-wide text-ink-400">
          {data.label}
        </span>
        <span className="text-[10px] text-ink-400">
          {data.stepCount} {data.stepCount === 1 ? 'step' : 'steps'}
        </span>
        <button
          className="pointer-events-auto rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[10px] font-semibold text-accent-700 hover:border-accent-400"
          onClick={event => {
            event.stopPropagation();
            data.onCollapse(data.stageIndex);
          }}
          title={`Collapse ${data.label} into a single box`}
          type="button"
        >
          Collapse
        </button>
      </div>
    </div>
  );
}

export function BuilderStagePlaceholderNode({ data }: NodeProps<BuilderStagePlaceholderData>) {
  const preview = data.stepLabels.slice(0, 3).join(', ');
  const remaining = data.stepLabels.length - 3;
  return (
    <button
      className={`w-[240px] cursor-pointer rounded-md border-2 border-dashed bg-slate-50 px-3 py-2.5 text-left hover:border-accent-400 ${
        data.hasIssue ? 'border-red-400' : 'border-slate-300'
      }`}
      onClick={() => data.onExpand(data.stageIndex)}
      title={data.stepLabels.join('\n')}
      type="button"
    >
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />
      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wide text-ink-500">
        {data.label} · collapsed
        {data.hasIssue && (
          <span className="font-bold text-red-600" title="A step in here has a preflight issue">!</span>
        )}
      </div>
      <div className="mt-1 font-medium text-ink-900">{data.nodeIds.length} steps</div>
      <div className="mt-0.5 truncate text-[10px] text-ink-500">
        {preview}{remaining > 0 ? ` +${remaining} more` : ''}
      </div>
      <div className="mt-1 text-[10px] font-semibold text-accent-700">Click to expand</div>
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </button>
  );
}
