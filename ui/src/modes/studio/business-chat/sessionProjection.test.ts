import { describe, expect, it } from 'vitest';

import { auditEventLabel, auditPayloadSummary, buildSessionTrace, filterAuditEvents, sourceActivityNodeIds } from './sessionProjection';

const audit = [
  { run_id: 'run-1', session_id: 's', node_id: 'search', event_type: 'node_end' as const, actor: 'system', payload: { results: 'list[2]' }, ts: '2026-08-24T00:00:01Z' },
  { run_id: 'run-1', session_id: 's', node_id: 'review', event_type: 'hitl_reject' as const, actor: 'ayush', payload: { reason: 'Wrong customer' }, ts: '2026-08-24T00:00:02Z' },
  { run_id: 'run-1', session_id: 's', node_id: 'search', event_type: 'node_reused' as const, actor: 'system', payload: {}, ts: '2026-08-24T00:00:03Z' },
];

describe('session trace projection', () => {
  it('filters and labels real audit event classes', () => {
    expect(filterAuditEvents(audit, 'approvals')).toHaveLength(1);
    expect(filterAuditEvents(audit, 'errors').map(event => event.event_type)).toEqual(['hitl_reject']);
    expect(filterAuditEvents(audit, 'reused')).toHaveLength(1);
    expect(auditEventLabel(audit[0])).toBe('Node completed');
    expect(auditPayloadSummary(audit[1])).toEqual(['Reason: Wrong customer']);
  });

  it('links a source only to activities with exact provenance', () => {
    const source = { id: 'doc:1', title: 'Handbook.pdf', kind: 'document' as const, selected: true, status: 'ready' as const };
    const activities = [{ nodeId: 'knowledge', nodeType: 'KnowledgeRetrieval', displayName: 'Read knowledge sources', agentRole: null, status: 'completed' as const, text: 'Read sources', sources: [{ title: 'Handbook.pdf · page 4' }], recoveryActions: [] }];
    expect(sourceActivityNodeIds(source, activities)).toEqual(['knowledge']);
    expect(sourceActivityNodeIds({ ...source, title: 'Other.pdf' }, activities)).toEqual([]);
  });

  it('exports summaries without raw node inputs or outputs', () => {
    const trace = buildSessionTrace({ title: 'Research', workflowLabel: 'Deep Research', run: null, audit, activities: [], sources: [], messageCount: 4 });
    expect(trace.session.message_count).toBe(4);
    expect(trace.audit[0]).toMatchObject({ node_id: 'search', summary: ['results: list[2]'] });
    expect(trace).not.toHaveProperty('inputs');
  });
});