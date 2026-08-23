import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SessionAuditPanel } from './SessionAuditPanel';
import type { RunDetail } from '../../../api/types';

const run: RunDetail = {
  run_id: 'run-1', session_id: 'session-1', workflow_name: 'research', status: 'completed', started_at: 1,
  ended_at: 3, duration_s: 2, node_count: 1, completed_node_count: 1, active_nodes: [], error: null,
  created_at: '2026-08-24T00:00:00Z', updated_at: '2026-08-24T00:00:02Z', inputs: {}, outputs: {},
  node_runs: { search: { node_id: 'search', type_name: 'WebSearchAgent', status: 'completed', input: {}, output: {}, started_at: 1, ended_at: 3, duration_s: 2, error: null } },
};

describe('SessionAuditPanel', () => {
  it('shows real run overview and switches to audit events', () => {
    const onTabChange = vi.fn();
    const { rerender } = render(<SessionAuditPanel title="Market research" collapsed={false} run={run} audit={[{ run_id: 'run-1', session_id: 'session-1', node_id: 'search', event_type: 'node_end', actor: 'workflow', payload: { source_count: 2 }, ts: '2026-08-24T00:00:02Z' }]} activities={[]} sources={[]} messageCount={3} workflowLabel="Deep Research" activeTab="overview" selectedNodeId={null} onCollapse={vi.fn()} onOpenHistory={vi.fn()} onNewChat={vi.fn()} onTabChange={onTabChange} onSelectNode={vi.fn()} onSelectSource={vi.fn()} onOpenTechnical={vi.fn()} />);
    expect(screen.getByText('Market research')).toBeVisible();
    expect(screen.getByText('3')).toBeVisible();
    fireEvent.click(screen.getByRole('tab', { name: 'Audit' }));
    expect(onTabChange).toHaveBeenCalledWith('audit');
    rerender(<SessionAuditPanel title="Market research" collapsed={false} run={run} audit={[{ run_id: 'run-1', session_id: 'session-1', node_id: 'search', event_type: 'node_end', actor: 'workflow', payload: { source_count: 2 }, ts: '2026-08-24T00:00:02Z' }]} activities={[]} sources={[]} messageCount={3} workflowLabel="Deep Research" activeTab="audit" selectedNodeId={null} onCollapse={vi.fn()} onOpenHistory={vi.fn()} onNewChat={vi.fn()} onTabChange={onTabChange} onSelectNode={vi.fn()} onSelectSource={vi.fn()} onOpenTechnical={vi.fn()} />);
    expect(screen.getByText('Node completed')).toBeVisible();
    expect(screen.getByText(/search · source count: 2/)).toBeVisible();
  });

  it('filters approvals and exposes safe trace actions', () => {
    const events = [
      { run_id: 'run-1', session_id: 'session-1', node_id: 'search', event_type: 'node_end' as const, actor: 'workflow', payload: { results: 'list[2]' }, ts: '2026-08-24T00:00:02Z' },
      { run_id: 'run-1', session_id: 'session-1', node_id: 'review', event_type: 'hitl_approve' as const, actor: 'ayush', payload: {}, ts: '2026-08-24T00:00:03Z' },
    ];
    render(<SessionAuditPanel title="Market research" collapsed={false} run={run} audit={events} activities={[]} sources={[]} messageCount={3} workflowLabel="Deep Research" activeTab="audit" selectedNodeId={null} onCollapse={vi.fn()} onOpenHistory={vi.fn()} onNewChat={vi.fn()} onTabChange={vi.fn()} onSelectNode={vi.fn()} onSelectSource={vi.fn()} onOpenTechnical={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Approvals' }));
    expect(screen.getByText('Approved')).toBeVisible();
    expect(screen.queryByText('Node completed')).not.toBeInTheDocument();
  });

  it('links exact source provenance to its activity and source row', () => {
    const onSelectNode = vi.fn(); const onSelectSource = vi.fn(); const onTabChange = vi.fn();
    const source = { id: 'document:1', title: 'Handbook.pdf', kind: 'document' as const, selected: true, status: 'ready' as const, accessed: true };
    const activity = { nodeId: 'knowledge', nodeType: 'KnowledgeRetrieval', displayName: 'Read knowledge sources', agentRole: null, status: 'completed' as const, text: 'Retrieved evidence', sources: [{ title: 'Handbook.pdf · page 2' }], recoveryActions: [] };
    render(<SessionAuditPanel title="Market research" collapsed={false} run={run} audit={[]} activities={[activity]} sources={[source]} messageCount={3} workflowLabel="Deep Research" activeTab="sources" selectedNodeId={null} onCollapse={vi.fn()} onOpenHistory={vi.fn()} onNewChat={vi.fn()} onTabChange={onTabChange} onSelectNode={onSelectNode} onSelectSource={onSelectSource} onOpenTechnical={vi.fn()} />);
    fireEvent.click(screen.getAllByRole('button', { name: /Handbook.pdf/ })[0]);
    expect(onSelectSource).toHaveBeenCalledWith('document:1');
    fireEvent.click(screen.getAllByRole('button', { name: 'Show activity' })[0]);
    expect(onSelectNode).toHaveBeenCalledWith('knowledge');
    expect(onTabChange).toHaveBeenCalledWith('activity');
  });
});