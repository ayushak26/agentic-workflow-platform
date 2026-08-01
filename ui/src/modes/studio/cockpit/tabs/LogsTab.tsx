import type { NodeRun } from '../../../../api/types';
import { clock } from '../node-render';
import { VirtualList } from '../VirtualList';

type LogLine = { ts: string; text: string };

function buildLogLines(nodeRun: NodeRun | undefined): LogLine[] {
  if (!nodeRun) return [];
  const lines: LogLine[] = [];
  if (nodeRun.started_at != null) {
    lines.push({ ts: clock(nodeRun.started_at), text: `${nodeRun.node_id} started (${nodeRun.type_name})` });
  }
  for (const selection of nodeRun.model_selections ?? []) {
    lines.push({
      ts: clock(nodeRun.started_at),
      text: `Model call ${selection.call_id}: requested ${selection.requested_model} → `
        + `${selection.actual_model}${selection.fallback ? ' (fallback)' : ''}`
        + `${selection.cache_hit ? ' [cache hit]' : ''}`,
    });
  }
  if (nodeRun.status === 'failed' && nodeRun.error) {
    lines.push({ ts: clock(nodeRun.ended_at), text: `Failed: ${nodeRun.error}` });
  } else if (nodeRun.ended_at != null) {
    lines.push({ ts: clock(nodeRun.ended_at), text: `${nodeRun.node_id} completed` });
  }
  return lines;
}

export function LogsTab({ nodeRun }: { nodeRun: NodeRun | undefined }) {
  const lines = buildLogLines(nodeRun);

  return (
    <div className="p-3 h-full flex flex-col min-h-0">
      <div className="flex-none mb-2 text-[11px] text-ink-500">
        Per-node execution timeline. Fine-grained step-by-step logs
        aren&rsquo;t captured by the runtime today — this shows the events
        that are: start/completion timestamps and any model selections.
      </div>
      <div className="flex-1 min-h-0">
        <VirtualList
          items={lines}
          itemHeight={28}
          className="h-full"
          emptyState={<div className="text-xs text-ink-500 px-1 py-2">No log entries yet.</div>}
          renderItem={(line) => (
            <div className="flex items-baseline gap-2 px-1 text-[11px] font-mono">
              <span className="text-ink-400 flex-none">{line.ts}</span>
              <span className="text-ink-700 truncate">{line.text}</span>
            </div>
          )}
        />
      </div>
    </div>
  );
}
