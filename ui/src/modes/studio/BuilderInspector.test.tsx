import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { NodeActions } from './BuilderInspector';

describe('NodeActions', () => {
  it('offers Test node and AI autofix without depending on a node type allowlist', async () => {
    const user = userEvent.setup();
    const onTest = vi.fn();
    const onAutofix = vi.fn();
    render(<NodeActions onTest={onTest} onAutofix={onAutofix} />);

    await user.click(screen.getByRole('button', { name: 'Test node' }));
    await user.click(screen.getByRole('button', { name: /Autofix node/ }));
    expect(onTest).toHaveBeenCalledOnce();
    expect(onAutofix).toHaveBeenCalledOnce();
  });

  it('disables autofix while a selected-node repair is running', () => {
    render(<NodeActions onTest={vi.fn()} onAutofix={vi.fn()} autofixing />);
    expect(screen.getByRole('button', { name: 'Fixing…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Test node' })).toBeEnabled();
  });
});