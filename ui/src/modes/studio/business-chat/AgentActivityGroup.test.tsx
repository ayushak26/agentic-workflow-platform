import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { AgentActivityGroup } from './AgentActivityGroup';

const activities = [
  { nodeId: 'knowledge', nodeType: 'KnowledgeRetrieval', displayName: 'Knowledge Source', agentRole: null, status: 'completed' as const, text: 'Retrieved 3 relevant passages.', durationSeconds: 1.2, tool: { kind: 'tool' as const, label: 'Knowledge Retrieval' }, recoveryActions: [] },
  { nodeId: 'image', nodeType: 'OpenAIImageGenerationAgent', displayName: 'Generate Answer Image', agentRole: null, status: 'running' as const, text: 'Generating the visual…', recoveryActions: [] },
];

describe('AgentActivityGroup', () => {
  it('summarizes activity and selects a real workflow node when expanded', () => {
    const onSelectNode = vi.fn();
    render(<AgentActivityGroup activities={activities} selectedNodeId={null} onSelectNode={onSelectNode} />);
    expect(screen.getByText('Working on your request')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: /Working on your request/ }));
    fireEvent.click(screen.getByRole('button', { name: /Knowledge Source/ }));
    expect(onSelectNode).toHaveBeenCalledWith('knowledge');
  });

  it('automatically expands around the selected node', () => {
    render(<AgentActivityGroup activities={activities} selectedNodeId="image" onSelectNode={vi.fn()} />);
    expect(screen.getByRole('button', { name: /Generate Answer Image/ })).toBeVisible();
  });
});