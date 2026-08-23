import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { NotebookSourcesPanel } from './NotebookSourcesPanel';

const sources = [
  { id: 'upload:one', title: 'Annual Report.pdf', kind: 'upload' as const, selected: true, status: 'ready' as const, referenced: true },
  { id: 'web:two', title: 'example.com/research', kind: 'web' as const, selected: false, status: 'outdated' as const },
];

describe('NotebookSourcesPanel', () => {
  it('distinguishes available, active and referenced sources', () => {
    const onToggle = vi.fn();
    render(<NotebookSourcesPanel sources={sources} notes={[]} collapsed={false} loading={false} onCollapse={vi.fn()} onToggle={onToggle} onAddSources={vi.fn()} onOpenNote={vi.fn()} onNewNote={vi.fn()} />);
    expect(screen.getByText('2 available · 1 active')).toBeVisible();
    expect(screen.getByText('Referenced', { selector: 'em' })).toBeVisible();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Use Annual Report.pdf in the next prompt' }));
    expect(onToggle).toHaveBeenCalledWith('upload:one');
  });

  it('filters source rows by status and context state', () => {
    render(<NotebookSourcesPanel sources={sources} notes={[]} collapsed={false} loading={false} onCollapse={vi.fn()} onToggle={vi.fn()} onAddSources={vi.fn()} onOpenNote={vi.fn()} onNewNote={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Attention' }));
    expect(screen.queryByText('Annual Report.pdf')).not.toBeInTheDocument();
    expect(screen.getByText('example.com/research')).toBeVisible();
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search sources' }), { target: { value: 'missing' } });
    expect(screen.getByText('No sources yet.')).toBeVisible();
  });

  it('opens the Session source usage view without changing source selection', () => {
    const onShowUsage = vi.fn();
    const onToggle = vi.fn();
    render(<NotebookSourcesPanel sources={sources} notes={[]} collapsed={false} loading={false} onCollapse={vi.fn()} onToggle={onToggle} onAddSources={vi.fn()} onShowUsage={onShowUsage} onOpenNote={vi.fn()} onNewNote={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Show usage for Annual Report.pdf' }));
    expect(onShowUsage).toHaveBeenCalledWith(sources[0]);
    expect(onToggle).not.toHaveBeenCalled();
  });

  it('removes an individual source through its lifecycle action', () => {
    const onRemoveSource = vi.fn();
    render(<NotebookSourcesPanel sources={sources} notes={[]} collapsed={false} loading={false} onCollapse={vi.fn()} onToggle={vi.fn()} onAddSources={vi.fn()} onRemoveSource={onRemoveSource} onOpenNote={vi.fn()} onNewNote={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Remove Annual Report.pdf' }));
    expect(onRemoveSource).toHaveBeenCalledWith(sources[0]);
  });
});