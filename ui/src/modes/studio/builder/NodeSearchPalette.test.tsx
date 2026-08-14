import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { Node } from 'reactflow';

import { NodeSearchPalette } from './NodeSearchPalette';
import type { WorkflowNodeData } from '../yaml-bridge';

function node(id: string, data: Partial<WorkflowNodeData> = {}): Node<WorkflowNodeData> {
  return {
    id,
    type: 'workflow',
    position: { x: 0, y: 0 },
    data: { nodeId: id, typeName: 'AITaskAgent', config: {}, ...data },
  };
}

const nodes = [
  node('classify_request', { experience: { display_name: 'Understand Customer Request' } }),
  node('notify_customer', { typeName: 'EmailAgent' }),
  node('update_case', {
    typeName: 'MCPToolAgent',
    config: { server_id: 'dynamics365', tool: 'update_case' },
  }),
];

function setup() {
  const onSelect = vi.fn();
  const onClose = vi.fn();
  render(<NodeSearchPalette nodes={nodes} onClose={onClose} onSelect={onSelect} />);
  return { onClose, onSelect, user: userEvent.setup() };
}

describe('NodeSearchPalette', () => {
  it('opens focused, listing every step', async () => {
    setup();
    expect(screen.getByPlaceholderText(/find a step/i)).toHaveFocus();
    expect(screen.getAllByRole('button')).toHaveLength(nodes.length);
  });

  it('narrows to what was typed and jumps to the first match on Enter', async () => {
    const { onSelect, user } = setup();
    await user.keyboard('dynamics');
    expect(screen.getAllByRole('button')).toHaveLength(1);
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledWith('update_case');
  });

  it('moves through the results with the arrow keys', async () => {
    const { onSelect, user } = setup();
    await user.keyboard('{ArrowDown}{ArrowDown}{Enter}');
    expect(onSelect).toHaveBeenCalledWith('update_case');
  });

  it('wraps around rather than dead-ending at the last result', async () => {
    const { onSelect, user } = setup();
    await user.keyboard('{ArrowUp}{Enter}');
    expect(onSelect).toHaveBeenCalledWith('update_case');
  });

  it('picks a step with the mouse', async () => {
    const { onSelect, user } = setup();
    await user.click(screen.getByText('Understand Customer Request'));
    expect(onSelect).toHaveBeenCalledWith('classify_request');
  });

  it('says so when nothing matches', async () => {
    const { user } = setup();
    await user.keyboard('zzz');
    expect(screen.getByText(/no step matches/i)).toBeInTheDocument();
    expect(screen.queryAllByRole('button')).toHaveLength(0);
  });

  it('closes on Escape and on a click outside', async () => {
    const { onClose, user } = setup();
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole('presentation'));
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
