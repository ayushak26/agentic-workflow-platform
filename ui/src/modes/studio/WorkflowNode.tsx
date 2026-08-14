import { memo } from 'react';
import { Handle, Position, type NodeProps } from 'reactflow';
import { ExecutionKindBadge } from './builder/ExecutionKindBadge';
import { nodeTypeLabel } from './node-presentation';
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
 *
 * At low zoom the card switches to a compact form (`data.compact`): one large
 * label and nothing else. Zooming out on a long workflow is only useful if the
 * result is still readable, and at 40% zoom the badge row and 11px subtitle are
 * noise rather than information — see the semantic-zoom tiers in Builder.
 */

export const WorkflowNode = memo(function WorkflowNode({
  data,
  selected,
}: NodeProps<WorkflowNodeData>) {
  const requestedModel = data.selectedModel ?? data.config.model;
  const businessLabel = data.experience?.display_name?.trim();
  const typeLabel = nodeTypeLabel(data.typeName, data.config);
  const mcpOperation = typeof data.mcpOperation === 'string' ? data.mcpOperation : '';
  const vertical = data.flowDirection === 'TB';

  const border = data.hasIssue
    ? 'border-red-400'
    : data.simulationState === 'ran'
      ? 'border-emerald-500'
      : data.simulationState === 'waiting'
        ? 'border-sky-400'
        : selected
          ? 'border-accent-600'
          : 'border-slate-200';

  // Incoming on the left (or top), outgoing on the right (or bottom): handles
  // follow the layout direction so a top-down graph doesn't route every edge
  // sideways out of the card and back again.
  const targetHandle = (
    <Handle
      type="target"
      position={vertical ? Position.Top : Position.Left}
      className="!bg-slate-400"
    />
  );
  const sourceHandle = (
    <Handle
      type="source"
      position={vertical ? Position.Bottom : Position.Right}
      className="!bg-slate-400"
    />
  );

  if (data.compact) {
    return (
      <div
        className={`flex min-h-[76px] min-w-[230px] flex-col justify-center rounded-md border-2 bg-white px-4 py-3 shadow-sm transition-opacity ${border} ${
          data.faded ? 'opacity-40' : 'opacity-100'
        }`}
      >
        {targetHandle}
        <div className="truncate text-[19px] font-semibold leading-tight text-ink-900">
          {businessLabel || data.nodeId}
        </div>
        <div className="mt-1 flex items-center gap-2 text-[13px] text-ink-500">
          <span className="truncate">{typeLabel}</span>
          {data.hasIssue && <span className="flex-none font-bold text-red-600">!</span>}
        </div>
        {sourceHandle}
      </div>
    );
  }

  return (
    <div
      className={`min-w-[230px] rounded-md border-2 bg-white px-4 py-3 shadow-sm transition-opacity ${border} ${
        data.faded ? 'opacity-40' : 'opacity-100'
      }`}
    >
      {targetHandle}

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

      {sourceHandle}
    </div>
  );
});
