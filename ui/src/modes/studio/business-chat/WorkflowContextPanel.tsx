import type { AgentActivity, WorkflowChatMeta } from './businessChatModel';
import { WorkflowProgress, type WorkflowProgressStep } from '../../../components/workflows/WorkflowProgress';
import { normalizeWorkflowStatus } from '../../../components/workflows/workflowStatus';

function progressSteps(meta: WorkflowChatMeta, activities: Record<string, AgentActivity>): WorkflowProgressStep[] {
  return meta.nodes.map(node => ({
    id: node.id,
    name: node.displayName,
    type: node.type,
    status: normalizeWorkflowStatus(activities[node.id]?.status),
    error: activities[node.id]?.error,
    tool: activities[node.id]?.tool?.label,
  }));
}

export function WorkflowExecutionStrip({
  meta,
  activities,
  selectedNodeId,
  onSelect,
}: {
  meta: WorkflowChatMeta;
  activities: Record<string, AgentActivity>;
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
}) {
  return (
    <WorkflowProgress steps={progressSteps(meta, activities)} selectedStepId={selectedNodeId} onSelectStep={onSelect} compact showSummary />
  );
}

export function WorkflowContextPanel({
  meta,
  activities,
  attemptLabel,
  selectedNodeId,
  onSelect,
  onClose,
}: {
  meta: WorkflowChatMeta;
  activities: Record<string, AgentActivity>;
  attemptLabel?: string;
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  onClose: () => void;
}) {
  return (
    <aside className="flex h-full w-72 flex-none flex-col border-l border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">Workflow context</h2>
          <p className="text-xs text-ink-400">
            {meta.nodes.length} steps{attemptLabel ? ` · ${attemptLabel}` : ''}
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-xs text-ink-500 hover:text-ink-900">Collapse</button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        <WorkflowProgress steps={progressSteps(meta, activities)} selectedStepId={selectedNodeId} onSelectStep={onSelect} showSummary={false} />
      </div>
    </aside>
  );
}