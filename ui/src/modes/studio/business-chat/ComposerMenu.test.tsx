import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ComposerMenu } from './ComposerMenu';

const items = [
  { id: 'analyze', label: 'Analyze sources', description: 'Compare evidence.' },
  { id: 'research', label: 'Research', description: 'Investigate deeply.' },
];

describe('ComposerMenu', () => {
  it('focuses the first item and supports arrow navigation and Escape', () => {
    const onClose = vi.fn();
    render(<ComposerMenu label="Choose a skill" items={items} onChoose={vi.fn()} onClose={onClose} />);
    const analyze = screen.getByRole('menuitem', { name: /Analyze sources/ });
    const research = screen.getByRole('menuitem', { name: /Research/ });
    expect(analyze).toHaveFocus();
    fireEvent.keyDown(analyze, { key: 'ArrowDown' });
    expect(research).toHaveFocus();
    fireEvent.keyDown(research, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('closes when clicking outside', () => {
    const onClose = vi.fn();
    render(<div><ComposerMenu label="Choose a skill" items={items} onChoose={vi.fn()} onClose={onClose} /><button type="button">Outside</button></div>);
    fireEvent.mouseDown(screen.getByRole('button', { name: 'Outside' }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('uses the full row for iconless skills and preserves the icon column for create items', () => {
    const { rerender } = render(<ComposerMenu label="Choose a skill" items={items} onChoose={vi.fn()} onClose={vi.fn()} />);
    const skill = screen.getByRole('menuitem', { name: /Analyze sources/ });
    expect(skill).toHaveClass('without-icon');
    expect(skill.querySelector('.chat-composer-menu-icon')).toBeNull();
    expect(skill.querySelector('.chat-composer-menu-content')).not.toBeNull();

    rerender(<ComposerMenu label="Create something" items={[{
      id: 'report', label: 'Report', description: 'A structured analysis.', icon: '▤',
    }]} onChoose={vi.fn()} onClose={vi.fn()} />);
    const createItem = screen.getByRole('menuitem', { name: /Report/ });
    expect(createItem).toHaveClass('has-icon');
    expect(createItem.querySelector('.chat-composer-menu-icon')).toHaveTextContent('▤');
  });
});