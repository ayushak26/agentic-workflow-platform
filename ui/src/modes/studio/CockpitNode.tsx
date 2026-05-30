import { Handle, Position, type NodeProps } from 'reactflow';
import type { WorkflowNodeData } from './yaml-bridge';
import type { NodeStatus } from './cockpit-state';

type CockpitNodeData = WorkflowNodeData & { status: NodeStatus };

const STATUS_STYLES: Record<NodeStatus, { border: string; pill: string; label: string }> = {
  pending: { border: 'border-slate-200', pill: 'bg-slate-100 text-ink-500', label: 'pending' },
  active:  { border: 'border-accent-600 animate-pulse', pill: 'bg-accent-600 text-white', label: 'running' },
  done:    { border: 'border-ok', pill: 'bg-ok text-white', label: 'done' },
  paused:  { border: 'border-warn', pill: 'bg-warn text-white', label: 'paused' },
  failed:  { border: 'border-bad', pill: 'bg-bad text-white', label: 'failed' },
};

export function CockpitNode({ data, selected }: NodeProps<CockpitNodeData>) {
  const s = STATUS_STYLES[data.status];
  return (
    <div className={`bg-white rounded-md border-2 shadow-sm px-4 py-3 min-w-[240px] ${s.border} ${selected ? 'ring-2 ring-accent-500' : ''}`}>
      <Handle type="target" position={Position.Top} className="!bg-slate-400" />
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs uppercase tracking-wide text-ink-500">{data.typeName}</div>
        <span className={`text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 ${s.pill}`}>{s.label}</span>
      </div>
      <div className="font-medium text-ink-900 mt-1">{data.nodeId}</div>
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400" />
    </div>
  );
}