import { businessActivityLabel, type AgentActivity, type WorkflowChatMeta } from './businessChatModel';

function stageStatus(activity: AgentActivity | undefined): { glyph: string; label: string; className: string } {
  if (!activity || activity.status === 'waiting') return { glyph: '○', label: 'Waiting', className: 'text-ink-400' };
  if (activity.status === 'running') return { glyph: '●', label: 'In progress', className: 'text-accent-700' };
  if (activity.status === 'failed') return { glyph: '!', label: 'Needs attention', className: 'text-bad' };
  if (activity.status === 'needs_input') return { glyph: '●', label: 'Waiting for input', className: 'text-amber-700' };
  return { glyph: '✓', label: 'Complete', className: 'text-emerald-700' };
}

export function WorkflowContextPanel({
  meta,
  activities,
  attemptLabel,
  runId,
  onClose,
}: {
  meta: WorkflowChatMeta;
  activities: Record<string, AgentActivity>;
  attemptLabel?: string;
  runId: string | null;
  onClose: () => void;
}) {
  const businessStages = meta.nodes.flatMap(node => {
    const label = businessActivityLabel(node);
    return label ? [{ node, label, activity: activities[node.id] }] : [];
  });

  return (
    <aside className="flex h-full w-72 flex-none flex-col border-l border-slate-200 bg-white">
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
        <div>
          <h2 className="text-sm font-semibold text-ink-900">Activity</h2>
          <p className="text-xs text-ink-400">
            {attemptLabel ?? 'Current request'}
          </p>
        </div>
        <button type="button" onClick={onClose} className="text-xs text-ink-500 hover:text-ink-900">Collapse</button>
      </div>
      <div className="flex-1 overflow-y-auto p-3">
        <div className="space-y-2">
          {businessStages.length === 0 && (
            <p className="rounded-lg bg-slate-50 px-3 py-4 text-sm text-ink-500">
              Activity will appear here while Chat works on your request.
            </p>
          )}
          {businessStages.map(({ node, label, activity }) => {
            const status = stageStatus(activity);
            return (
              <div key={node.id} className="flex items-start gap-3 rounded-lg border border-slate-100 bg-white px-3 py-3">
                <span className={`mt-0.5 text-sm ${status.className}`} aria-hidden>{status.glyph}</span>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-ink-800">{label}</p>
                  <p className={`mt-0.5 text-xs ${status.className}`}>{status.label}</p>
                </div>
              </div>
            );
          })}
          {runId && (
            <a
              href={`/cockpit/${encodeURIComponent(runId)}`}
              className="mt-3 block w-full rounded-md border border-slate-200 px-3 py-2 text-center text-xs font-medium text-ink-600 hover:bg-slate-50"
            >
              Open technical execution
            </a>
          )}
        </div>
      </div>
    </aside>
  );
}