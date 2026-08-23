import { expect, test, type Page, type Route } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';

const SCREENSHOT_ROOT = path.resolve('../qa-results/screenshots');

function workflow(index: number) {
  const id = `workflow-${String(index).padStart(3, '0')}`;
  return {
    name: id,
    description: `Scale workflow ${index} — unicode Ω 🚀 and long content `.repeat(index % 4 + 1),
    use_case: index % 2 ? 'research' : 'operations',
    version: '1.0',
    node_count: (index % 25) + 1,
    updated_at: new Date(1_700_000_000_000 + index * 1000).toISOString(),
    library: {
      title: `Workflow ${String(index).padStart(3, '0')}`,
      summary: `Workflow summary ${index}`,
      purpose: ['Automated QA'],
      suitable_for: ['Scale testing'],
      not_suitable_for: [],
      outputs: index % 3 === 0 ? ['pdf'] : ['text'],
      input_types: ['text'],
      typical_duration: { minimum_minutes: 1, maximum_minutes: 5 },
      human_reviews: { count: index % 2, labels: [] },
      evidence_policy: null,
      visibility_status: index % 5 === 0 ? 'approved' : 'draft',
      owner_team: null,
      declared: true,
    },
    readiness: { level: 'ready', items: [] },
  };
}

function run(index: number) {
  const status = ['completed', 'failed', 'running', 'paused', 'rejected'][index % 5];
  const created = new Date(1_720_000_000_000 + index * 60_000).toISOString();
  return {
    run_id: `run-${String(index).padStart(3, '0')}`,
    session_id: 'qa-session',
    workflow_name: `Workflow ${String(index % 34).padStart(3, '0')}`,
    status,
    started_at: 1_720_000_000 + index * 60,
    ended_at: status === 'running' || status === 'paused' ? null : 1_720_000_010 + index * 60,
    duration_s: status === 'running' ? null : 10 + index,
    node_count: 10,
    completed_node_count: status === 'failed' ? 4 : status === 'running' ? 6 : 10,
    active_nodes: status === 'running' ? ['process'] : [],
    last_completed_node: 'prepare',
    failed_node: status === 'failed' ? 'process' : null,
    error: status === 'failed' ? `Synthetic failure ${index}` : null,
    created_at: created,
    updated_at: created,
    origin: 'direct',
    history_visibility: 'global',
  };
}

const chatYaml = `name: QA Chat Workflow
description: Deterministic browser test workflow
version: '1.0'
entry: start
exit: end
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      chatbot_name: QA Chat
      welcome_message: Ask a question
      message_placeholder: Ask a question
  - id: end
    type: EndAgent
    config:
      mode: workflow_result
      outputs:
        - key: result
          value_from: '{{outputs.start.message}}'
edges:
  - from: start
    to: end
`;

type MockOptions = {
  workflows?: number;
  runs?: number;
  workflowError?: number;
};

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

async function installApi(page: Page, options: MockOptions = {}) {
  const workflows = Array.from({ length: options.workflows ?? 400 }, (_, index) => workflow(index + 1));
  const runs = Array.from({ length: options.runs ?? 120 }, (_, index) => run(index + 1)).reverse();
  const requestCounts = new Map<string, number>();
  // React Strict Mode replays the mount effect in development. Fail both
  // initial requests so the error state is observable; the explicit Retry
  // request is the first success.
  let remainingWorkflowErrors = options.workflowError ? 2 : 0;
  const transcript: Array<Record<string, unknown>> = [];
  const conversation = {
    id: 'conversation-workflow-001', workflow_source: 'shared', workflow_id: 'workflow-001',
    created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
  };
  let chatRunId = 'chat-run-1';

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/auth/')) {
      await route.continue();
      return;
    }
    requestCounts.set(url.pathname, (requestCounts.get(url.pathname) ?? 0) + 1);

    if (url.pathname === '/auth/me') return json(route, { username: 'qa-user' });
    if (url.pathname === '/api/workflows' && request.method() === 'GET') {
      if (options.workflowError && remainingWorkflowErrors > 0) {
        remainingWorkflowErrors -= 1;
        return json(route, { detail: 'Synthetic workflow failure' }, options.workflowError);
      }
      return json(route, workflows);
    }
    if (url.pathname === '/api/workflows/chat-catalog') return json(route, [workflows[0]]);
    if (url.pathname === '/api/chat-workspace/experiences') return json(route, { experiences: [] });
    if (url.pathname === '/api/chat-workflows') return json(route, { workflows: [] });
    if (url.pathname === '/api/chat-workflows/adapters/by-name/workflow-001') {
      return json(route, { workflow_name: 'workflow-001', yaml: chatYaml, adapted: true });
    }
    if (url.pathname === '/api/chat-conversations/resolve' && request.method() === 'POST') {
      return json(route, { conversation, messages: transcript });
    }
    if (url.pathname === `/api/chat-conversations/${conversation.id}/messages` && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = transcript.find(message => message.id === body.message_id);
      if (existing) return json(route, existing, 201);
      const message = {
        id: body.message_id, role: body.role, content: body.content, run_id: body.run_id ?? null,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      transcript.push(message);
      return json(route, message, 201);
    }
    if (url.pathname === '/api/llm/models') return json(route, { models: [] });
    if (url.pathname === '/api/workflows/run' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      chatRunId = String(body.run_id ?? 'chat-run-1');
      return json(route, { run_id: chatRunId, status: 'running' });
    }
    if (url.pathname === `/api/runs/${chatRunId}/events`) {
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 1\nevent: run_completed\ndata: {"type":"run_completed","run_id":"${chatRunId}","event_id":1}\n\n`,
      });
    }
    if (url.pathname === '/api/runs/mine') return json(route, { count: runs.length, runs });
    if (/^\/api\/runs\/mine\/run-\d+$/.test(url.pathname)) {
      const id = url.pathname.split('/').pop()!;
      const summary = runs.find(item => item.run_id === id) ?? runs[0];
      return json(route, { run: { ...summary, inputs: { query: 'qa' }, outputs: { result: 'ok' }, node_runs: {} }, audit: [] });
    }
    if (url.pathname === `/api/runs/mine/${chatRunId}`) {
      return json(route, {
        run: { ...run(1), run_id: chatRunId, status: 'completed', inputs: {}, outputs: { result: 'answer' }, node_runs: {} },
        audit: [],
      });
    }
    if (url.pathname === `/api/runs/mine/${chatRunId}/pending-gate`) return json(route, { run_id: chatRunId, paused: false });
    if (url.pathname === '/api/node-types') return json(route, []);
    if (url.pathname === '/api/knowledge/collections') return json(route, []);
    if (url.pathname === '/api/knowledge/doc-types') return json(route, { doc_types: [] });
    if (url.pathname === '/api/eval/golden-set') return json(route, { name: 'document_qa', n: 0, examples: [] });
    if (url.pathname === '/api/eval/history') return json(route, { scorecards: [] });
    if (url.pathname === '/api/eval/workflow-golden-set') return json(route, { name: 'verder_customer_triage', n: 0, cases: [] });
    if (url.pathname === '/api/cost-admin/overview') return json(route, {
      period_days: 30, total_usd: 0, total_calls: 0, input_tokens: 0, output_tokens: 0,
      by_provider: [], by_model: [], by_workflow: [], by_session: [], daily_trend: [],
    });
    return json(route, { detail: `Unhandled QA route ${url.pathname}` }, 404);
  });
  return requestCounts;
}

function runtimeErrors(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  return errors;
}

async function openApp(page: Page) {
  await page.goto('/chat');
  await expect(page.getByRole('button', { name: 'Workflow Studio' })).toBeVisible();
}

test.beforeAll(async () => {
  await fs.mkdir(SCREENSHOT_ROOT, { recursive: true });
});

test('all four real modes survive rapid switching without state leakage or request storms', async ({ page }) => {
  const errors = runtimeErrors(page);
  const counts = await installApi(page);
  await openApp(page);

  const modes = ['Knowledge Studio', 'Evaluation Lab', 'Cost Management', 'Workflow Studio'] as const;
  await page.evaluate((labels) => {
    for (let cycle = 0; cycle < 5; cycle += 1) {
      for (const label of labels) {
        const button = [...document.querySelectorAll('button')]
          .find(element => element.textContent?.trim() === label) as HTMLButtonElement | undefined;
        button?.click();
      }
    }
  }, modes);
  await expect(page.locator('.app-topbar').getByText('Workflow Studio', { exact: true })).toBeVisible();

  expect(counts.get('/api/knowledge/collections') ?? 0).toBeLessThanOrEqual(5);
  expect(counts.get('/api/eval/golden-set') ?? 0).toBeLessThanOrEqual(5);
  expect(counts.get('/api/cost-admin/overview') ?? 0).toBeLessThanOrEqual(5);
  expect(errors).toEqual([]);
});

test('400-workflow library remains searchable and responsive at scale', async ({ page }, testInfo) => {
  const errors = runtimeErrors(page);
  const counts = await installApi(page, { workflows: 400 });
  await openApp(page);
  await page.getByRole('link', { name: 'Workflows' }).click();
  await expect(page.getByText('400 workflows')).toBeVisible();

  const search = page.getByRole('searchbox', { name: 'Search workflows' });
  await search.fill('Workflow 400');
  await expect(page.getByRole('heading', { name: 'Workflow 400' })).toBeVisible();
  await search.fill('does-not-exist');
  await expect(page.getByText('No workflow matches all selected filters.')).toBeVisible();
  await search.fill('');

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(counts.get('/api/workflows')).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(SCREENSHOT_ROOT, `workflow-library-${testInfo.project.name}.png`), fullPage: true });
});

test('120-run history supports filtering, deep-link selection, and bounded requests', async ({ page }, testInfo) => {
  const errors = runtimeErrors(page);
  const counts = await installApi(page, { runs: 120 });
  await openApp(page);
  await page.getByRole('link', { name: 'Workflow runs' }).click();
  const viewportWidth = testInfo.project.use.viewport?.width ?? 1920;
  if (viewportWidth < 1150) {
    const expandHistory = page.getByTitle('Expand run history');
    await expect(expandHistory).toBeVisible();
    await expandHistory.click();
  }
  await expect(page.getByRole('heading', { name: 'Run History' })).toBeVisible();
  await page.getByPlaceholder('Search runs…').fill('Workflow 005');
  await expect(page.getByText('Workflow 005').first()).toBeVisible();
  await page.getByPlaceholder('Search runs…').fill('no-such-run');
  await expect(page.getByText('No runs match your filters.')).toBeVisible();
  await page.getByPlaceholder('Search runs…').fill('');
  counts.set('/api/runs/mine', 0);
  await page.goto('/workflow-runs/run-005?tab=inputs');
  await expect(page.locator('section').filter({ hasText: 'Inputs received' }).getByText('qa', { exact: true })).toBeVisible();

  expect(counts.get('/api/runs/mine')).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
  await page.screenshot({ path: path.join(SCREENSHOT_ROOT, `run-history-${testInfo.project.name}.png`), fullPage: true });
});

test('workflow API errors render a recoverable state instead of a blank screen', async ({ page }) => {
  const errors = runtimeErrors(page);
  await installApi(page, { workflowError: 500 });
  await openApp(page);
  await page.getByRole('link', { name: 'Workflows' }).click();
  await expect(page.getByText(/Synthetic workflow failure/)).toBeVisible();
  await page.getByRole('button', { name: 'Retry' }).click();
  await expect(page.getByRole('heading', { name: 'Workflow Library' })).toBeVisible();
  expect(errors).toEqual([
    'console: Failed to load resource: the server responded with a status of 500 (Internal Server Error)',
    'console: Failed to load resource: the server responded with a status of 500 (Internal Server Error)',
  ]);
});

test('Business Chat transcript survives refresh', async ({ page }) => {
  await installApi(page);
  await page.goto('/chat/shared/workflow-001');
  const composer = page.getByPlaceholder('Ask anything about your sources…');
  await expect(composer).toBeVisible();
  await composer.fill('Persistence probe Ω 🚀');
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.getByText('Persistence probe Ω 🚀')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Persistence probe Ω 🚀')).toBeVisible();
});

test('major screens have no horizontal document overflow', async ({ page }, testInfo) => {
  const errors = runtimeErrors(page);
  await installApi(page);
  await openApp(page);
  const screens = [
    ['/workflows', 'workflow-list'],
    ['/workflow-runs', 'run-history'],
    ['/chat/shared/workflow-001', 'chat'],
  ] as const;
  for (const [url, name] of screens) {
    await page.goto(url);
    await page.waitForLoadState('networkidle');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, `${name} horizontal overflow`).toBeLessThanOrEqual(1);
    await page.screenshot({ path: path.join(SCREENSHOT_ROOT, `${name}-${testInfo.project.name}.png`), fullPage: true });
  }
  expect(errors).toEqual([]);
});