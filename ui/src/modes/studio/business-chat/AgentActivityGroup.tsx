import { useEffect, useMemo, useState } from 'react';

import type { AgentActivity } from './businessChatModel';

function statusGlyph(status: AgentActivity['status']): string {
  if (status === 'completed') return '✓';
  if (status === 'reused') return '↻';
  if (status === 'failed') return '!';
  if (status === 'running') return '●';
  if (status === 'needs_input') return '…';
  return '○';
}

function activityHeadline(activities: AgentActivity[]): string {
  if (activities.some(activity => activity.status === 'needs_input')) return 'Waiting for your approval';
  if (activities.some(activity => activity.status === 'running')) return 'Working on your request';
  if (activities.some(activity => activity.status === 'failed')) return 'Some activity needs attention';
  const reused = activities.filter(activity => activity.status === 'reused').length;
  if (reused > 0) return `Completed with ${reused} reused step${reused === 1 ? '' : 's'}`;
  return 'Completed workflow activity';
}

export function AgentActivityGroup({
  activities,
  selectedNodeId,
  onSelectNode,
}: {
  activities: AgentActivity[];
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const visible = useMemo(() => activities.filter(activity => activity.displayName && activity.text), [activities]);
  const toolCount = visible.filter(activity => activity.tool).length;
  const duration = visible.reduce((total, activity) => total + (activity.durationSeconds ?? 0), 0);

  useEffect(() => {
    if (selectedNodeId && visible.some(activity => activity.nodeId === selectedNodeId)) setExpanded(true);
  }, [selectedNodeId, visible]);

  if (visible.length === 0) return null;
  const current = visible.find(activity => activity.status === 'needs_input')
    ?? visible.find(activity => activity.status === 'running')
    ?? [...visible].reverse().find(activity => activity.status === 'failed')
    ?? visible[visible.length - 1];

  return (
    <section className="chat-agent-activity" aria-label="Workflow activity">
      <button type="button" className="chat-agent-activity-summary" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>
        <span className={`chat-agent-activity-glyph is-${current.status}`}>{statusGlyph(current.status)}</span>
        <span><strong>{activityHeadline(visible)}</strong><small>{visible.length} action{visible.length === 1 ? '' : 's'}{toolCount ? ` · ${toolCount} tool${toolCount === 1 ? '' : 's'}` : ''}{duration > 0 ? ` · ${duration.toFixed(1)}s` : ''}</small></span>
        <em>{expanded ? 'Hide' : 'View activity'}</em>
      </button>
      {expanded && <div className="chat-agent-activity-steps">
        {visible.map(activity => (
          <button
            type="button"
            key={activity.nodeId}
            data-node-id={activity.nodeId}
            className={`chat-agent-activity-step is-${activity.status} ${selectedNodeId === activity.nodeId ? 'is-selected' : ''}`}
            onClick={() => onSelectNode(activity.nodeId)}
          >
            <span>{statusGlyph(activity.status)}</span>
            <span><strong>{activity.displayName}</strong><small>{activity.text}</small>{activity.tool && <small>{activity.tool.label}{activity.tool.detail ? ` · ${activity.tool.detail}` : ''}</small>}</span>
            {activity.durationSeconds != null && <em>{activity.durationSeconds.toFixed(1)}s</em>}
          </button>
        ))}
      </div>}
    </section>
  );
}