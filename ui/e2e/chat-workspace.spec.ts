import { expect, test, type Page, type Route } from '@playwright/test';


const OBJECTIVE = 'Explain recursion in simple language.';
const WORKFLOW_ID = 'cwf_workspace_1';
const ANSWER = 'Recursion is when a process solves a problem by calling a smaller version of itself.';
const EXPERIENCES = [
  ['document_qa', 'Ask Questions About My Documents'],
  ['research_analyst', 'Research Analyst'],
  ['research_to_presentation', 'Research to Presentation'],
  ['research_to_pdf', 'Research to PDF Report'],
  ['meeting_intelligence', 'Meeting / Interview Intelligence'],
  ['customer_feedback', 'Customer Feedback Analysis'],
  ['competitive_intelligence', 'Competitive Intelligence'],
  ['contract_policy', 'Contract / Policy Understanding'],
  ['long_document', 'Long Document Assistant'],
  ['study_assistant', 'Study / Learning Assistant'],
  ['executive_brief', 'Executive Brief Generator'],
  ['results_interpreter', 'Data / Results Interpreter'],
  ['product_requirements', 'Product Requirements Assistant'],
  ['content_repurposing', 'Content Repurposing'],
  ['proposal_generator', 'Proposal Generator'],
  ['due_diligence', 'Due-Diligence Assistant'],
  ['troubleshooting', 'Incident / Troubleshooting Assistant'],
  ['decision_support', 'Decision Support'],
  ['chat_workflow', 'Chat → Workflow Execution'],
  ['multi_workflow_project', 'Multi-Workflow Project'],
] as const;


async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}


async function installApi(page: Page) {
  const runBodies: Array<Record<string, unknown>> = [];
  const messages: Array<Record<string, unknown>> = [];
  const llmYaml = `name: AI Workspace
description: Lightweight adapter
version: '1.0'
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      chatbot_name: AI Workspace
      welcome_message: Ask a question.
      allow_attachments: true
  - id: answer
    type: TransformAgent
    config:
      mode: ai
      model: auto
      prompt_template: '{{outputs.start.message}}'
  - id: reply
    type: EndAgent
    config:
      mode: chat_response
      chat_message: '${ANSWER}'
edges:
  - from: start
    to: answer
  - from: answer
    to: reply
`;
  const workflow = {
    id: WORKFLOW_ID, slug: 'workspace-test', name: 'AI Workspace', description: 'Lightweight adapter',
    source: 'imported', visibility: 'private', status: 'private', source_workflow_name: null,
    output_compatibility: { supported: true, detected_types: ['text'], fallback_to_text: false, warnings: [] },
    created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
  };
  let runId = 'workspace-run';

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/auth/')) return route.continue();
    if (url.pathname === '/auth/me') return json(route, { username: 'workspace-user' });
    if (url.pathname === '/api/workflows/chat-catalog') return json(route, []);
    if (url.pathname === '/api/chat-workflows' && request.method() === 'GET') return json(route, { workflows: [] });
    if (url.pathname === '/api/chat-workspace/experiences') return json(route, { experiences: EXPERIENCES.map(([id, title]) => ({
      id, title, examples: [], default_plan: 'files', existing_workflow: null, capabilities: [],
    })) });
    if (url.pathname === '/api/chat-workspace/prepare' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      if (body.previous_run_id) {
        return json(route, {
          plan: {
            kind: 'artifact', title: 'AI Workspace Presentation', reason: 'Reuse previous result.',
            yaml: 'type: PowerPointProposalSlides', existing_workflow: null, experience_id: null,
            missing_requirements: [], capabilities: ['pptx', 'previous_result'],
          },
          workflow: { ...workflow, id: 'cwf_follow_up', name: 'AI Workspace Presentation' },
        }, 201);
      }
      expect(body.objective).toBe(OBJECTIVE);
      expect(body.has_attachments).toBe(false);
      return json(route, {
        plan: {
          kind: 'llm', title: 'AI Workspace',
          reason: 'A lightweight LLM workflow is sufficient.', yaml: llmYaml,
          existing_workflow: null, experience_id: null,
          missing_requirements: [], capabilities: ['llm'],
        },
        workflow,
      }, 201);
    }
    if (url.pathname === `/api/chat-workflows/${WORKFLOW_ID}`) return json(route, { ...workflow, yaml: llmYaml });
    if (url.pathname === '/api/chat-conversations/resolve') return json(route, {
      conversation: {
        id: 'conversation-workspace', workflow_source: 'private', workflow_id: WORKFLOW_ID,
        created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
      },
      messages,
    });
    if (url.pathname === '/api/llm/models') return json(route, { models: [] });
    if (url.pathname === '/api/chat-conversations/conversation-workspace/messages' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = messages.find(item => item.id === body.message_id);
      if (existing) return json(route, existing, 201);
      const message = {
        id: body.message_id, role: body.role, content: body.content, run_id: body.run_id ?? null,
        created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
      };
      messages.push(message);
      return json(route, message, 201);
    }
    if (url.pathname === '/api/workflows/run' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      runBodies.push(body);
      runId = String(body.run_id ?? runId);
      return json(route, { run_id: runId, status: 'running' });
    }
    if (url.pathname === `/api/runs/${runId}/events`) return route.fulfill({
      status: 200, contentType: 'text/event-stream',
      body: `id: 1\nevent: run_completed\ndata: {"type":"run_completed","run_id":"${runId}","event_id":1}\n\n`,
    });
    if (url.pathname === `/api/runs/mine/${runId}`) return json(route, {
      run: {
        run_id: runId, session_id: 'workspace-user', workflow_name: 'AI Workspace', status: 'completed',
        started_at: 1, ended_at: 2, duration_s: 1, node_count: 3, completed_node_count: 3,
        active_nodes: [], error: null, created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:01Z',
        inputs: { message: OBJECTIVE }, outputs: { message: ANSWER },
        node_types: { start: 'StartAgent', answer: 'TransformAgent', reply: 'EndAgent' },
        node_runs: { reply: { output: { chat_message: ANSWER } } },
      }, audit: [],
    });
    if (url.pathname === `/api/runs/mine/${runId}/pending-gate`) return json(route, { run_id: runId, paused: false });
    return json(route, { detail: `Unhandled workspace route ${url.pathname}` }, 404);
  });
  return { runBodies };
}


test('workflow-neutral chat selects the lightweight path and persists the result', async ({ page }) => {
  const state = await installApi(page);
  await page.goto('/chat');
  await expect(page.getByRole('heading', { name: 'What do you want to accomplish?' })).toBeVisible();
  await page.getByPlaceholder(/Analyze these documents/).fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Start in Chat →' }).click();

  await expect(page).toHaveURL(new RegExp(`/chat/private/${WORKFLOW_ID}`));
  const composer = page.getByPlaceholder('Ask a question…');
  await expect(composer).toHaveValue(OBJECTIVE);
  await page.getByRole('button', { name: /Send/ }).click();
  const assistant = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last();
  await expect(assistant).toBeVisible();

  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    origin: 'chat_saved_workflow', workflow_id: WORKFLOW_ID,
    inputs: { message: expect.stringContaining(OBJECTIVE) },
  });
  expect(String(state.runBodies[0].workflow_yaml)).toContain('TransformAgent');
  expect(String(state.runBodies[0].workflow_yaml)).not.toContain('KnowledgeRetrieval');
  expect(String(state.runBodies[0].workflow_yaml)).not.toContain('RAGAgent');

  await page.reload();
  const restoredUser = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: OBJECTIVE }).first();
  const restoredAssistant = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last();
  await expect(restoredUser).toBeVisible();
  await expect(restoredAssistant).toBeVisible();
  expect(state.runBodies).toHaveLength(1);
});


test('all 20 workspace experiences are discoverable and the selected style reaches prepare', async ({ page }) => {
  await installApi(page);
  await page.goto('/chat');

  const selector = page.getByLabel('Workspace experience');
  await expect(selector.locator('option')).toHaveCount(21);
  for (const [id, title] of EXPERIENCES) {
    await expect(selector.locator(`option[value="${id}"]`)).toHaveText(title);
  }

  await selector.selectOption('product_requirements');
  await page.getByPlaceholder(/Analyze these documents/).fill(OBJECTIVE);
  const prepareRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname === '/api/chat-workspace/prepare'
  ));
  await page.getByRole('button', { name: 'Start in Chat →' }).click();
  expect((await prepareRequest).postDataJSON()).toMatchObject({
    objective: OBJECTIVE,
    experience_id: 'product_requirements',
  });
});


test('artifact follow-up prepares a new workflow from the previous run', async ({ page }) => {
  await installApi(page);
  await page.goto('/chat');
  await page.getByPlaceholder(/Analyze these documents/).fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Start in Chat →' }).click();
  await page.getByRole('button', { name: /Send/ }).click();
  const assistant = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last();
  await expect(assistant).toBeVisible();

  const followUp = page.getByPlaceholder('Ask a question…');
  await followUp.fill('Turn that into an executive presentation');
  const prepareRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname === '/api/chat-workspace/prepare'
    && Boolean((request.postDataJSON() as Record<string, unknown>).previous_run_id)
  ));
  await page.getByRole('button', { name: /Send/ }).click();
  const request = await prepareRequest;
  expect(request.postDataJSON()).toMatchObject({
    objective: 'Turn that into an executive presentation',
    preferred_output: 'pptx',
  });
  expect((request.postDataJSON() as Record<string, unknown>).previous_run_id).toBeTruthy();
  await expect(page).toHaveURL(/\/chat\/private\/cwf_follow_up/);
});