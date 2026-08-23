import { useMemo, useState } from 'react';

import type { AuditEvent, RunDetail } from '../../../api/types';
import { CopyButton } from '../../../components/CopyButton';
import type { AgentActivity } from './businessChatModel';
import type { WorkspaceSource } from './chatWorkspaceModel';
import { auditEventLabel, auditPayloadSummary, buildSessionTrace, filterAuditEvents, sourceActivityNodeIds, type AuditFilter } from './sessionProjection';

export type SessionTab = 'overview' | 'activity' | 'sources' | 'audit';

function statusGlyph(status: AgentActivity['status']): string {
  if (status === 'completed' || status === 'reused') return '✓';
  if (status === 'failed') return '!';
  if (status === 'running') return '●';
  if (status === 'needs_input') return '…';
  return '○';
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  return seconds < 60 ? `${seconds.toFixed(1)}s` : `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

export function SessionAuditPanel({
  title,
  collapsed,
  run,
  audit,
  activities,
  sources,
  messageCount,
  workflowLabel,
  activeTab,
  selectedNodeId,
  onCollapse,
  onOpenHistory,
  onNewChat,
  onTabChange,
  onSelectNode,
  onSelectSource,
  onOpenTechnical,
}: {
  title: string;
  collapsed: boolean;
  run: RunDetail | null;
  audit: AuditEvent[];
  activities: AgentActivity[];
  sources: WorkspaceSource[];
  messageCount: number;
  workflowLabel: string;
  activeTab: SessionTab;
  selectedNodeId: string | null;
  onCollapse: () => void;
  onOpenHistory: () => void;
  onNewChat: () => void;
  onTabChange: (tab: SessionTab) => void;
  onSelectNode: (nodeId: string) => void;
  onSelectSource: (sourceId: string) => void;
  onOpenTechnical: (nodeId?: string | null) => void;
}) {
  const [auditFilter, setAuditFilter] = useState<AuditFilter>('all');
  const tab = activeTab;
  const selectedSources = sources.filter(source => source.selected);
  const accessedSources = sources.filter(source => source.accessed || source.referenced);
  const models = useMemo(() => [...new Set(Object.values(run?.node_runs ?? {}).flatMap(node => (
    node.model_selections?.map(selection => selection.actual_model) ?? []
  )))], [run]);
  const filteredAudit = useMemo(() => filterAuditEvents(audit, auditFilter), [audit, auditFilter]);
  const trace = useMemo(() => buildSessionTrace({ title, workflowLabel, run, audit, activities, sources, messageCount }), [activities, audit, messageCount, run, sources, title, workflowLabel]);
  const traceText = useMemo(() => JSON.stringify(trace, null, 2), [trace]);

  function downloadTrace() {
    const blob = new Blob([traceText], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${run?.run_id ?? 'chat-session'}-trace.json`;
    link.click();
    URL.revokeObjectURL(url);
  }

  if (collapsed) {
    return <aside className="chat-rail chat-rail--collapsed chat-rail--right"><button type="button" onClick={onCollapse} aria-label="Expand Session panel">‹</button><span>Session</span></aside>;
  }

  return (
    <aside className="chat-rail chat-session" aria-label="Session panel">
      <div className="chat-rail-header">
        <div><h2>Session</h2><p>{run ? run.status.replace('_', ' ') : 'Ready'}</p></div>
        <button type="button" onClick={onCollapse} aria-label="Collapse Session panel">›</button>
      </div>
      <div className="chat-session-tabs" role="tablist" aria-label="Session views">
        {(['overview', 'activity', 'sources', 'audit'] as const).map(value => (
          <button type="button" role="tab" aria-selected={tab === value} className={tab === value ? 'is-active' : ''} key={value} onClick={() => onTabChange(value)}>{value[0].toUpperCase() + value.slice(1)}</button>
        ))}
      </div>
      <div className="chat-session-content">
        {tab === 'overview' && <>
          <section className="chat-session-hero"><span className={`chat-session-status is-${run?.status ?? 'ready'}`}>{run?.status ?? 'Ready'}</span><h3>{title}</h3><p>{workflowLabel}</p></section>
          <dl className="chat-session-metrics">
            <div><dt>Messages</dt><dd>{messageCount}</dd></div>
            <div><dt>Active sources</dt><dd>{selectedSources.length}</dd></div>
            <div><dt>Completed steps</dt><dd>{run?.completed_node_count ?? 0}</dd></div>
            <div><dt>Duration</dt><dd>{formatDuration(run?.duration_s)}</dd></div>
          </dl>
          <section className="chat-session-section"><h3>Execution</h3><p>{models.length ? models.join(', ') : 'Workflow defaults'}</p><p>{run ? `Attempt ${run.attempt ?? 1}${run.reused_node_count ? ` · ${run.reused_node_count} reused` : ''}` : 'No run started yet'}</p></section>
          <div className="chat-session-actions"><button type="button" onClick={onOpenHistory}>Chat history</button><button type="button" onClick={onNewChat}>New chat</button></div>
          <div className="chat-trace-actions"><CopyButton text={traceText} label="Copy trace" copiedLabel="Trace copied" /><button type="button" onClick={downloadTrace}>Export trace</button>{run && <button type="button" onClick={() => onOpenTechnical(selectedNodeId)}>Inspect run</button>}</div>
        </>}
        {tab === 'activity' && <div className="chat-session-list">
          {activities.length === 0 && <p className="chat-muted">Agent and tool activity will appear after a run starts.</p>}
          {activities.map(activity => <details key={activity.nodeId} open={selectedNodeId === activity.nodeId} className={`chat-session-event is-${activity.status} ${selectedNodeId === activity.nodeId ? 'is-selected' : ''}`}><summary onClick={() => onSelectNode(activity.nodeId)}><span>{statusGlyph(activity.status)}</span><div><strong>{activity.displayName}</strong><small>{activity.text || activity.status.replace('_', ' ')}</small></div>{activity.durationSeconds != null && <em>{formatDuration(activity.durationSeconds)}</em>}</summary>{activity.tool && <p>{activity.tool.label}{activity.tool.detail ? ` · ${activity.tool.detail}` : ''}</p>}{activity.error && <pre>{activity.error}</pre>}</details>)}
          {run && <button type="button" className="chat-technical-link" onClick={() => onOpenTechnical(selectedNodeId)}>Open technical execution</button>}
        </div>}
        {tab === 'sources' && <div className="chat-session-source-groups">
          <section><h3>Connected <span>{selectedSources.length}</span></h3>{selectedSources.length ? selectedSources.map(source => { const nodeIds = sourceActivityNodeIds(source, activities); return <div className="chat-session-source" key={source.id}><button type="button" onClick={() => onSelectSource(source.id)}><strong>{source.title}</strong><small>{source.kind} · active for the next prompt</small></button>{nodeIds.length > 0 && <button type="button" onClick={() => { onSelectNode(nodeIds[0]); onTabChange('activity'); }}>Show activity</button>}</div>; }) : <p className="chat-muted">No active sources.</p>}</section>
          <section><h3>Accessed or referenced <span>{accessedSources.length}</span></h3>{accessedSources.length ? accessedSources.map(source => { const nodeIds = sourceActivityNodeIds(source, activities); return <div className="chat-session-source" key={source.id}><button type="button" onClick={() => onSelectSource(source.id)}><strong>{source.title}</strong><small>{source.referenced ? 'Cited in a response' : 'Accessed by the run'}</small></button>{nodeIds.length > 0 && <button type="button" onClick={() => { onSelectNode(nodeIds[0]); onTabChange('activity'); }}>Show activity</button>}</div>; }) : <p className="chat-muted">No source has been proven accessed yet.</p>}</section>
        </div>}
        {tab === 'audit' && <div className="chat-session-list">
          <div className="chat-audit-filters" aria-label="Audit filters">{(['all', 'activity', 'approvals', 'errors', 'reused'] as const).map(value => <button type="button" key={value} className={auditFilter === value ? 'is-active' : ''} onClick={() => setAuditFilter(value)}>{value[0].toUpperCase() + value.slice(1)}</button>)}</div>
          {filteredAudit.length === 0 && <p className="chat-muted">No audit events match this filter.</p>}
          {[...filteredAudit].reverse().map((event, index) => { const summary = auditPayloadSummary(event); return <details open={Boolean(event.node_id) && selectedNodeId === event.node_id} className={`chat-audit-event ${event.node_id && selectedNodeId === event.node_id ? 'is-selected' : ''}`} key={`${event.ts}:${event.node_id}:${index}`}><summary onClick={() => { if (event.node_id) onSelectNode(event.node_id); }}><time>{new Date(event.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time><div><strong>{auditEventLabel(event)}</strong><small>{event.node_id || event.actor}{summary.length ? ` · ${summary.join(' · ')}` : ''}</small></div></summary><dl><div><dt>Actor</dt><dd>{event.actor}</dd></div><div><dt>Run</dt><dd>{event.run_id}</dd></div></dl>{summary.length > 0 && <ul>{summary.map(item => <li key={item}>{item}</li>)}</ul>}<details className="chat-audit-payload"><summary>View safe payload</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details></details>; })}
          <p className="chat-audit-note">Audit payloads record shape and bounded decision metadata, never prompt or proposal content.</p>
        </div>}
      </div>
    </aside>
  );
}