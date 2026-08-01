import { Handle, Position, type NodeProps } from 'reactflow';
import type { StagePlaceholderData } from './graph-collapse';

/**
 * Stands in for a collapsed stage's member nodes — click to expand again.
 * Rendered instead of (not on top of) the stage's real nodes, so it never
 * changes the graph's overall footprint by more than one node's worth of
 * space.
 */
export function StagePlaceholderNode({ data }: NodeProps<StagePlaceholderData>) {
  const { counts } = data;
  return (
    <div className="bg-slate-50 rounded-md border-2 border-dashed border-slate-300 px-3 py-2.5 w-[220px] cursor-pointer hover:border-accent-400">
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />
      <div className="text-[10px] uppercase tracking-wide text-ink-500">{data.label} · collapsed</div>
      <div className="font-medium text-ink-900 mt-1">{data.nodeIds.length} nodes</div>
      <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px] text-ink-500">
        {counts.completed > 0 && <span>{counts.completed} completed</span>}
        {counts.running > 0 && <span className="text-accent-700">{counts.running} running</span>}
        {counts.failed > 0 && <span className="text-bad">{counts.failed} failed</span>}
        {counts.waiting > 0 && <span>{counts.waiting} waiting</span>}
      </div>
      <div className="mt-1.5 text-[10px] text-accent-700">Click to expand</div>
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
}
