import type { AuditEvent, RunDetail } from '../../../api/types';
import type { AgentActivity } from './businessChatModel';
import type { WorkspaceSource } from './chatWorkspaceModel';

export type AuditFilter = 'all' | 'activity' | 'approvals' | 'errors' | 'reused';

const APPROVAL_EVENTS = new Set(['hitl_approve', 'hitl_reject', 'hitl_edit']);

export function auditEventLabel(event: AuditEvent): string {
  const labels: Record<string, string> = {
    node_start: 'Node started', node_end: 'Node completed', node_reused: 'Node reused', node_error: 'Node failed',
    hitl_approve: 'Approved', hitl_reject: 'Rejected', hitl_edit: 'Edited and continued',
  };
  return labels[event.event_type] ?? event.event_type.replaceAll('_', ' ').replace(/\b\w/g, value => value.toUpperCase());
}

export function filterAuditEvents(events: AuditEvent[], filter: AuditFilter): AuditEvent[] {
  if (filter === 'all') return events;
  if (filter === 'approvals') return events.filter(event => APPROVAL_EVENTS.has(event.event_type));
  if (filter === 'errors') return events.filter(event => event.event_type === 'node_error' || event.event_type === 'hitl_reject');
  if (filter === 'reused') return events.filter(event => event.event_type === 'node_reused');
  return events.filter(event => event.event_type.startsWith('node_'));
}

export function auditPayloadSummary(event: AuditEvent): string[] {
  const payload = event.payload ?? {};
  if (event.event_type === 'hitl_reject' && typeof payload.reason === 'string') return [`Reason: ${payload.reason}`];
  if (event.event_type === 'hitl_edit') {
    return [
      typeof payload.source === 'string' ? `Source: ${payload.source}` : null,
      typeof payload.content_chars === 'number' ? `${payload.content_chars.toLocaleString()} edited characters` : null,
      typeof payload.source_document_name === 'string' ? `Document: ${payload.source_document_name}` : null,
    ].filter((value): value is string => Boolean(value));
  }
  return Object.entries(payload).slice(0, 4).map(([key, value]) => `${key.replaceAll('_', ' ')}: ${String(value)}`);
}

function baseActivitySourceTitle(title: string): string {
  return title.split(' · page ')[0].split(' · Page ')[0].split(' · section ')[0].trim().toLowerCase();
}

export function sourceActivityNodeIds(source: WorkspaceSource, activities: AgentActivity[]): string[] {
  const sourceTitle = source.title.trim().toLowerCase();
  return activities.flatMap(activity => {
    const matches = activity.sources?.some(item => (
      baseActivitySourceTitle(item.title) === sourceTitle
      || Boolean(source.sourceUrl && item.url === source.sourceUrl)
    ));
    return matches ? [activity.nodeId] : [];
  });
}

export function buildSessionTrace({
  title, workflowLabel, run, audit, activities, sources, messageCount,
}: {
  title: string;
  workflowLabel: string;
  run: RunDetail | null;
  audit: AuditEvent[];
  activities: AgentActivity[];
  sources: WorkspaceSource[];
  messageCount: number;
}) {
  return {
    exported_at: new Date().toISOString(),
    session: { title, workflow: workflowLabel, message_count: messageCount },
    run: run ? {
      run_id: run.run_id, session_id: run.session_id, status: run.status, attempt: run.attempt ?? 1,
      started_at: run.started_at, ended_at: run.ended_at, duration_s: run.duration_s,
      completed_node_count: run.completed_node_count, reused_node_count: run.reused_node_count ?? 0,
    } : null,
    sources: sources.map(source => ({
      id: source.id, title: source.title, kind: source.kind, active: source.selected,
      accessed: Boolean(source.accessed), referenced: Boolean(source.referenced),
      activity_node_ids: sourceActivityNodeIds(source, activities),
    })),
    activity: activities.filter(activity => activity.displayName).map(activity => ({
      node_id: activity.nodeId, label: activity.displayName, status: activity.status,
      duration_s: activity.durationSeconds ?? null, tool: activity.tool?.label ?? null,
      source_count: activity.sources?.length ?? 0,
    })),
    audit: audit.map(event => ({
      timestamp: event.ts, event_type: event.event_type, label: auditEventLabel(event),
      node_id: event.node_id, actor: event.actor, summary: auditPayloadSummary(event),
    })),
  };
}