import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../../../api/client';
import type { PromptTemplate } from '../../../api/types';
import { PromptTemplateLibrary } from './PromptTemplateLibrary';

vi.mock('../../../api/client', () => ({ api: {
  listPromptTemplates: vi.fn(), createPromptTemplate: vi.fn(), updatePromptTemplate: vi.fn(),
  duplicatePromptTemplate: vi.fn(), favoritePromptTemplate: vi.fn(), deletePromptTemplate: vi.fn(),
} }));

const template: PromptTemplate = {
  id: 'builtin_summary', title: 'Focused summary', description: 'Summarize for an audience',
  category: 'Summarize', content: 'Summarize {{topic}} for {{audience}}.',
  variables: ['topic', 'audience'], favorite: false, built_in: true,
  created_at: '2026-01-01', updated_at: '2026-01-01',
};

describe('PromptTemplateLibrary', () => {
  beforeEach(() => { vi.clearAllMocks(); vi.mocked(api.listPromptTemplates).mockResolvedValue({ templates: [template] }); });

  it('renders variable fields and inserts the filled prompt', async () => {
    const user = userEvent.setup();
    const onInsert = vi.fn();
    render(<PromptTemplateLibrary onClose={vi.fn()} onInsert={onInsert} />);
    await user.click(await screen.findByRole('button', { name: /Focused summary/ }));
    await user.type(screen.getByLabelText('topic'), 'AI safety');
    await user.type(screen.getByLabelText('audience'), 'executives');
    await user.click(screen.getByRole('button', { name: 'Insert into chat' }));
    expect(onInsert).toHaveBeenCalledWith('Summarize AI safety for executives.');
  });

  it('creates a custom template with editable placeholders', async () => {
    const custom = { ...template, id: 'pt_1', title: 'My prompt', built_in: false, category: 'Writing' as const };
    vi.mocked(api.createPromptTemplate).mockResolvedValueOnce(custom);
    const user = userEvent.setup();
    render(<PromptTemplateLibrary onClose={vi.fn()} onInsert={vi.fn()} />);
    await user.click(await screen.findByRole('button', { name: '+ Create' }));
    await user.type(screen.getByLabelText('Template title'), 'My prompt');
    await user.click(screen.getByLabelText('Template content'));
    await user.paste('Write about {{topic}}');
    await user.click(screen.getByRole('button', { name: 'Save template' }));
    expect(api.createPromptTemplate).toHaveBeenCalledWith(expect.objectContaining({
      title: 'My prompt', content: 'Write about {{topic}}', category: 'Writing',
    }));
    expect(await screen.findByText('My prompt')).toBeInTheDocument();
  });

  it('duplicates an immutable built-in template', async () => {
    const copy = { ...template, id: 'pt_copy', title: 'Focused summary copy', built_in: false };
    vi.mocked(api.duplicatePromptTemplate).mockResolvedValueOnce(copy);
    const user = userEvent.setup();
    render(<PromptTemplateLibrary onClose={vi.fn()} onInsert={vi.fn()} />);
    await screen.findByText('Focused summary');
    await user.click(screen.getByRole('button', { name: 'Duplicate' }));
    expect(api.duplicatePromptTemplate).toHaveBeenCalledWith('builtin_summary');
    expect(await screen.findByText('Focused summary copy')).toBeInTheDocument();
  });
});