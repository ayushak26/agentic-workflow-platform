import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import type { WorkflowNodeData } from './yaml-bridge';

export const WorkflowNode = memo(function WorkflowNode({
  data,
  selected,
}: NodeProps<WorkflowNodeData>) {
  const requestedModel = data.selectedModel ?? data.config.model;
  return (
    <div
      className={`min-w-[220px] rounded-md border-2 bg-white px-4 py-3 shadow-sm transition-opacity ${
        data.hasIssue
          ? 'border-red-400'
          : selected
            ? 'border-accent-600'
            : 'border-slate-200'
      } ${data.faded ? 'opacity-40' : 'opacity-100'}`}
    >
      {/* Left handle: incoming edges (canvas flows left to right) */}
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wide text-ink-500">
            {data.typeName}
          </div>
          <div className="mt-1 truncate font-medium text-ink-900">{data.nodeId}</div>
        </div>
        {data.hasIssue && (
          <span
            className="mt-0.5 inline-flex h-4 w-4 flex-none items-center justify-center rounded-full bg-red-100 text-[10px] font-bold text-red-700"
            title="This node has a preflight issue"
          >
            !
          </span>
        )}
      </div>
      {typeof requestedModel === 'string' && requestedModel && (
        <div
          className={`mt-2 inline-flex rounded-full px-2 py-0.5 text-[10px] ${
            requestedModel === 'auto'
              ? 'bg-accent-50 text-accent-700'
              : 'bg-slate-100 text-ink-500'
          }`}
        >
          {requestedModel === 'auto'
            ? 'Best possible LLM · Auto'
            : requestedModel}
        </div>
      )}
      {typeof data.downstreamCount === 'number' && data.downstreamCount > 1 && (
        <div className="mt-1 text-[10px] text-ink-400">
          Fans out to {data.downstreamCount} nodes
        </div>
      )}

      {/* Right handle: outgoing edges */}
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
});
