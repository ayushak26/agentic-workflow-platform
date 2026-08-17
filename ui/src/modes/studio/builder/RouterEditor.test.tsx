import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { RouterEditor } from './RouterEditor';

function setup(config: Record<string, unknown>) {
  const onChange = vi.fn();
  render(
    <RouterEditor config={config} contract={null} onChange={onChange} operators={null} />,
  );
  return { onChange, user: userEvent.setup() };
}

describe('RouterEditor', () => {
  it('shows only the mode picker for a fresh node with no mode set', () => {
    setup({});
    expect(screen.getByText(/on a field value/i)).toBeInTheDocument();
    expect(screen.getByText(/on conditions/i)).toBeInTheDocument();
    expect(screen.queryByText(/otherwise, send to/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/kept for existing workflows/i)).not.toBeInTheDocument();
  });

  it('reveals the field-mode editor and fallback once a mode is chosen', async () => {
    const { onChange, user } = setup({});
    await user.click(screen.getByText(/on a field value/i));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: 'field' }));
  });

  it('still shows the legacy-mode banner for an existing saved router', () => {
    setup({ mode: 'rule', rules: [{ name: 'default', default: true }] });
    expect(screen.getByText(/kept for existing workflows/i)).toBeInTheDocument();
    expect(screen.getByText(/otherwise, send to/i)).toBeInTheDocument();
  });

  it('shows the field-mode editor and fallback for an existing field-mode router', () => {
    setup({ mode: 'field', route_field: 'outputs.x.intent', branches: {} });
    expect(screen.getByText(/otherwise, send to/i)).toBeInTheDocument();
    expect(screen.queryByText(/kept for existing workflows/i)).not.toBeInTheDocument();
  });
});
