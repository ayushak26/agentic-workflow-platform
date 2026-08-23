import type { AgentActivity, WorkflowChatMeta, WorkflowChatNode } from './businessChatModel';

function glyph(status: AgentActivity['status'] | 'waiting'): string {
  return ({ completed: '✓', reused: '↻', running: '●', failed: '×', needs_input: '!', waiting: '○' })[status];
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
    <div className="flex items-center gap-1 overflow-x-auto py-1" aria-label="Workflow execution">
      {meta.nodes.map((node, index) => {
        const status = activities[node.id]?.status ?? 'waiting';
        return (
          <div key={node.id} className="flex items-center gap-1">
            {index > 0 && <span className="text-ink-300">→</span>}
            <button
              type="button"
              title={`${node.displayName}: ${status}`}
              onClick={() => onSelect(node.id)}
              className={`whitespace-nowrap rounded-full px-2 py-1 text-[11px] ${
                selectedNodeId === node.id ? 'bg-accent-100 text-accent-800' : 'text-ink-500 hover:bg-slate-100'
              } ${status === 'failed' ? 'text-bad' : status === 'running' ? 'font-medium text-accent-700' : ''}`}
            >
              {node.displayName} {glyph(status)}
            </button>
          </div>
        );
      })}
    </div>
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
        {meta.nodes.map(node => (
          <ContextNode
            key={node.id}
            node={node}
            activity={activities[node.id]}
            selected={selectedNodeId === node.id}
            onSelect={() => onSelect(node.id)}
          />
        ))}
      </div>
    </aside>
  );
}

function ContextNode({ node, activity, selected, onSelect }: {
  node: WorkflowChatNode;
  activity?: AgentActivity;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = activity?.status ?? 'waiting';
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`mb-1.5 flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left ${selected ? 'bg-accent-50 ring-1 ring-accent-200' : 'hover:bg-slate-50'}`}
    >
      <span className={`mt-0.5 w-4 text-center text-xs ${status === 'failed' ? 'text-bad' : status === 'running' ? 'text-accent-600' : 'text-ink-400'}`}>{glyph(status)}</span>
      <span className="min-w-0">
        <span className="block truncate text-xs font-medium text-ink-800">{node.displayName}</span>
        <span className="block truncate text-[11px] capitalize text-ink-400">{status.replace('_', ' ')}</span>
      </span>
    </button>
  );
}