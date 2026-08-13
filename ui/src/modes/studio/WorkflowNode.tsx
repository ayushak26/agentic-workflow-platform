import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { ExecutionKindBadge } from './builder/ExecutionKindBadge';
import type { WorkflowNodeData } from './yaml-bridge';

/**
 * A step on the canvas.
 *
 * The business label is the headline and the technical type is the subtitle
 * (§16, §17): the graph should read as the business process — "Understand
 * Customer Request", "Check Automation Safety", "Route Customer Request" — with
 * the primitive that implements it available but secondary.
 *
 * The execution-kind badge is what makes the automation boundary visible
 * without opening anything: model, rule, external action, person.
 */

// Business-language names for the core primitives, matching the palette.
const TYPE_LABELS: Record<string, string> = {
  WorkflowInputAgent: 'Input',
  AITaskAgent: 'AI Task',
  DecisionAgent: 'Decision',
  RouterAgent: 'Router',
  DataTransformAgent: 'Transform',
  HumanInLoopAgent: 'Human Review',
  EmailAgent: 'Email',
  MCPToolAgent: 'MCP Tool',
};

export const WorkflowNode = memo(function WorkflowNode({
  data,
  selected,
}: NodeProps<WorkflowNodeData>) {
  const requestedModel = data.selectedModel ?? data.config.model;
  const businessLabel = data.experience?.display_name?.trim();
  const baseLabel = TYPE_LABELS[data.typeName] ?? data.typeName;
  // An MCP step says which system it reaches, because "MCP Tool" alone tells a
  // reader nothing: "MCP Tool · Dynamics CRM" is the useful subtitle.
  const serverId = typeof data.config.server_id === 'string' ? data.config.server_id : '';
  const typeLabel = serverId ? `${baseLabel} · ${serverId}` : baseLabel;
  const mcpOperation = typeof data.mcpOperation === 'string' ? data.mcpOperation : '';

  const border = data.hasIssue
    ? 'border-red-400'
    : data.simulationState === 'ran'
      ? 'border-emerald-500'
      : data.simulationState === 'waiting'
        ? 'border-sky-400'
        : selected
          ? 'border-accent-600'
          : 'border-slate-200';

  return (
    <div
      className={`min-w-[230px] rounded-md border-2 bg-white px-4 py-3 shadow-sm transition-opacity ${border} ${
        data.faded ? 'opacity-40' : 'opacity-100'
      }`}
    >
      {/* Left handle: incoming edges (canvas flows left to right) */}
      <Handle type="target" position={Position.Left} className="!bg-slate-400" />

      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate font-medium text-ink-900">
            {businessLabel || data.nodeId}
          </div>
          <div className="mt-0.5 truncate text-[11px] text-ink-500">
            {typeLabel}
            {businessLabel && (
              <span className="ml-1 font-mono text-[10px] text-ink-400">
                {data.nodeId}
              </span>
            )}
          </div>
        </div>
        {data.hasIssue && (
          <span
            className="mt-0.5 inline-flex h-4 w-4 flex-none items-center justify-center rounded-full bg-red-100 text-[10px] font-bold text-red-700"
            title="This step has a preflight issue"
          >
            !
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-1">
        {data.executionKind && <ExecutionKindBadge kind={data.executionKind} />}
        {mcpOperation && (
          <span
            className={`inline-flex rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${
              mcpOperation === 'read'
                ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                : 'border-amber-200 bg-amber-50 text-amber-800'
            }`}
            title={
              mcpOperation === 'read'
                ? 'Reads from the connected system. Changes nothing.'
                : 'Changes data in the connected system.'
            }
          >
            {mcpOperation}
          </span>
        )}
        {typeof requestedModel === 'string' && requestedModel && (
          <span
            className={`inline-flex rounded-full px-2 py-0.5 text-[9px] ${
              requestedModel === 'auto'
                ? 'bg-accent-50 text-accent-700'
                : 'bg-slate-100 text-ink-500'
            }`}
          >
            {requestedModel === 'auto' ? 'Best available model' : requestedModel}
          </span>
        )}
        {data.simulationState === 'ran' && (
          <span className="inline-flex rounded-full bg-emerald-50 px-2 py-0.5 text-[9px] text-emerald-700">
            ran
          </span>
        )}
        {data.simulationState === 'waiting' && (
          <span className="inline-flex rounded-full bg-sky-50 px-2 py-0.5 text-[9px] text-sky-700">
            waiting for a person
          </span>
        )}
      </div>

      {typeof data.downstreamCount === 'number' && data.downstreamCount > 1 && (
        <div className="mt-1 text-[10px] text-ink-400">
          Fans out to {data.downstreamCount} steps
        </div>
      )}

      {/* Right handle: outgoing edges */}
      <Handle type="source" position={Position.Right} className="!bg-slate-400" />
    </div>
  );
});
