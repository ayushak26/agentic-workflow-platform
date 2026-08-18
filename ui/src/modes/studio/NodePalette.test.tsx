import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { NodeTypeManifest } from '../../api/types';

import { NodePalette } from './NodePalette';

function manifest(overrides: Partial<NodeTypeManifest> = {}): NodeTypeManifest {
  return {
    type_name: 'TransformAgent',
    description: 'Pure LLM transform: summarize, classify, rewrite, extract.',
    category: 'Control & Flow',
    icon: 'topology',
    family: 'core',
    execution_kind: 'ai',
    uses_ai: true,
    external_action: false,
    about: {},
    presets: [],
    input_schema: {},
    output_schema: {},
    config_schema: {},
    ...overrides,
  } as NodeTypeManifest;
}

const types = [
  manifest(),
  manifest({ type_name: 'AITaskAgent', category: 'Core Building Blocks', description: 'Deprecated.' }),
  manifest({ type_name: 'RouterAgent', category: 'Core Building Blocks' }),
  manifest({ type_name: 'StartAgent', category: 'Core Building Blocks', description: 'How this workflow begins.' }),
  manifest({ type_name: 'EndAgent', category: 'Core Building Blocks', description: 'What this workflow returns.' }),
  manifest({ type_name: 'WorkflowInputAgent', category: 'Core Building Blocks', description: 'Deprecated.' }),
];

describe('NodePalette', () => {
  it('never lists AITaskAgent, deprecated in favor of TransformAgent', () => {
    render(<NodePalette onAdd={vi.fn()} types={types} />);
    expect(screen.queryByText('AI Task')).not.toBeInTheDocument();
    expect(screen.getByText('Router')).toBeInTheDocument();
  });

  it('keeps AITaskAgent hidden even when a search would otherwise match it', async () => {
    const user = userEvent.setup();
    render(<NodePalette onAdd={vi.fn()} types={types} />);
    await user.type(screen.getByLabelText(/search node types/i), 'task');
    expect(screen.queryByText('AI Task')).not.toBeInTheDocument();
  });

  it('lists Start and End as business-language labels', () => {
    render(<NodePalette onAdd={vi.fn()} types={types} />);
    expect(screen.getByText('Start')).toBeInTheDocument();
    expect(screen.getByText('End')).toBeInTheDocument();
  });

  it('never lists WorkflowInputAgent, deprecated in favor of Start', () => {
    render(<NodePalette onAdd={vi.fn()} types={types} />);
    expect(screen.queryByText('Input')).not.toBeInTheDocument();
  });
});
