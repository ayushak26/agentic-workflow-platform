import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatSessionsPanel } from './ChatSessionsPanel';

const chat = {
  id: 'chat-1', title: 'Pump review', createdAt: '2026-08-23T00:00:00Z', updatedAt: '2026-08-23T00:00:00Z',
  workflowId: 'pump-workflow', workflowSource: 'shared' as const, conversationId: 'conversation-1', runId: 'run-1',
};

describe('ChatSessionsPanel', () => {
  it('renders browser-local sessions and opens the selected session', () => {
    const onOpen = vi.fn();
    render(<ChatSessionsPanel chats={[chat]} activeChatId={chat.id} collapsed={false} onCollapse={vi.fn()} onNew={vi.fn()} onOpen={onOpen} onRename={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByRole('complementary', { name: 'Sessions panel' })).toBeVisible();
    expect(screen.queryByText('Studio')).not.toBeInTheDocument();
    fireEvent.click(screen.getByText('Pump review').closest('button') as HTMLButtonElement);
    expect(onOpen).toHaveBeenCalledWith(chat);
  });

  it('keeps management actions in the right rail', () => {
    const onNew = vi.fn(); const onRename = vi.fn(); const onDelete = vi.fn();
    render(<ChatSessionsPanel chats={[chat]} activeChatId="other" collapsed={false} onCollapse={vi.fn()} onNew={onNew} onOpen={vi.fn()} onRename={onRename} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole('button', { name: '＋ New session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Rename Pump review' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete Pump review' }));
    expect(onNew).toHaveBeenCalledOnce();
    expect(onRename).toHaveBeenCalledWith(chat);
    expect(onDelete).toHaveBeenCalledWith(chat);
  });
});