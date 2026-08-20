import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { api } from '../../api/client';
import { FALLBACK_FILE_CAPABILITIES } from './fileInputUtils';
import { RunDialog } from './RunDialog';

vi.mock('../../api/client', () => ({
  api: {
    workflowFileCapabilities: vi.fn(),
    validateWorkflow: vi.fn(),
    uploadWorkflowFiles: vi.fn(),
  },
}));

const navigateMock = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigateMock };
});

// A chatbot-mode Start node declares no `fields:` and no top-level `message`
// input — this is the exact shape workflows/w03_technical_service_case.yaml
// ships with. Regression coverage for: the dialog must collect a message
// for this shape, since nothing else in the platform will.
const CHATBOT_WORKFLOW_YAML = `
name: Chatbot Test Workflow
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      chatbot_name: Service Support
      welcome_message: Tell me what's happening.
      allow_attachments: true
edges: []
entry: start
exit: start
`;

function renderDialog() {
  render(
    <MemoryRouter>
      <RunDialog
        workflowName="Chatbot Test Workflow"
        workflowYaml={CHATBOT_WORKFLOW_YAML}
        inputs={{ attachments: { type: 'file', required: false, multiple: true } }}
        onClose={() => {}}
      />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  navigateMock.mockReset();
  vi.mocked(api.workflowFileCapabilities).mockResolvedValue(FALLBACK_FILE_CAPABILITIES);
  vi.mocked(api.validateWorkflow).mockResolvedValue({ valid: true, issues: [] } as never);
});

describe('RunDialog with a chatbot-mode Start node', () => {
  it('renders a message field labelled with the chatbot name and welcome message', () => {
    renderDialog();
    expect(screen.getByText(/Service Support message/)).toBeInTheDocument();
    expect(screen.getByText("Tell me what's happening.")).toBeInTheDocument();
  });

  it('blocks the run and shows an error when the message is left empty', async () => {
    renderDialog();
    await userEvent.click(screen.getByRole('button', { name: /test & run workflow/i }));

    expect(await screen.findByText(/enter the message/i)).toBeInTheDocument();
    expect(api.validateWorkflow).not.toHaveBeenCalled();
    expect(navigateMock).not.toHaveBeenCalled();
  });

  it('sends the typed message as inputs.message when launching', async () => {
    renderDialog();
    const field = screen.getByPlaceholderText(/type the message/i);
    await userEvent.type(field, 'My pump is making a loud noise.');
    await userEvent.click(screen.getByRole('button', { name: /test & run workflow/i }));

    await waitFor(() => expect(navigateMock).toHaveBeenCalled());
    const [, options] = navigateMock.mock.calls[0];
    expect(options.state.inputs).toMatchObject({ message: 'My pump is making a loud noise.' });
  });
});
