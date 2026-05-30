import { Handle, Position, type NodeProps } from 'reactflow';
import type { WorkflowNodeData } from './yaml-bridge';

export function WorkflowNode({ data, selected }: NodeProps<WorkflowNodeData>) {
  return (
    <div
      className={`bg-white rounded-md border-2 shadow-sm px-4 py-3 min-w-[220px] ${
        selected ? 'border-accent-600' : 'border-slate-200'
      }`}
    >
      {/* Top handle: incoming edges */}
      <Handle type="target" position={Position.Top} className="!bg-slate-400" />

      <div className="text-xs uppercase tracking-wide text-ink-500">
        {data.typeName}
      </div>
      <div className="font-medium text-ink-900 mt-1">{data.nodeId}</div>

      {/* Bottom handle: outgoing edges */}
      <Handle type="source" position={Position.Bottom} className="!bg-slate-400" />
    </div>
  );
}