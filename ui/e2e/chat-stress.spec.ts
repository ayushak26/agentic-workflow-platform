import { expect, test, type Page, type Route } from '@playwright/test';

type ChatRecord = {
  id: string;
  slug: string;
  name: string;
  description: string;
  source: 'generated' | 'imported' | 'existing';
  visibility: 'private';
  status: 'private' | 'publish_requested' | 'published';
  source_workflow_name: string | null;
  output_compatibility: {
    supported: boolean;
    detected_types: Array<'text' | 'code' | 'pdf' | 'docx'>;
    fallback_to_text: boolean;
    warnings: string[];
  };
  created_at: string;
  updated_at: string;
};

const chatYaml = `name: Random QA Chat
description: Isolated 100-chat stress workflow
version: '1.0'
entry: start
exit: end
nodes:
  - id: start
    type: StartAgent
    config:
      mode: chatbot
      chatbot_name: Random QA Chat
      welcome_message: Ask a question to exercise this private workflow.
      message_placeholder: Ask a question
      suggested_questions:
        - Summarize the latest result
        - Compare the available options
  - id: answer
    type: TransformAgent
    selected_model: gpt-5-mini
    allowed_models: [gpt-5-mini, claude-haiku-4-5]
    config:
      mode: ai
      model: gpt-5-mini
      prompt_template: '{{outputs.start.message}}'
  - id: end
    type: EndAgent
    config:
      mode: workflow_result
      outputs:
        - key: result
          value_from: '{{outputs.answer.text}}'
edges:
  - from: start
    to: answer
  - from: answer
    to: end
`;

function seededRandom(seed: number) {
  let value = seed >>> 0;
  return () => {
    value = (value * 1664525 + 1013904223) >>> 0;
    return value / 0x1_0000_0000;
  };
}

function randomChats(count = 100): ChatRecord[] {
  const random = seededRandom(0x5eed_2026);
  const adjectives = ['Amber', 'Brisk', 'Cobalt', 'Delta', 'Emerald', 'Fjord', 'Golden', 'Helix'];
  const nouns = ['Analyst', 'Planner', 'Researcher', 'Reviewer', 'Router', 'Writer', 'Advisor', 'Helper'];
  const outputTypes: ChatRecord['output_compatibility']['detected_types'][number][] = ['text', 'code', 'pdf', 'docx'];
  return Array.from({ length: count }, (_, offset) => {
    const number = offset + 1;
    const adjective = adjectives[Math.floor(random() * adjectives.length)];
    const noun = nouns[Math.floor(random() * nouns.length)];
    const output = outputTypes[Math.floor(random() * outputTypes.length)];
    const status: ChatRecord['status'] = number % 11 === 0
      ? 'publish_requested'
      : number % 17 === 0 ? 'published' : 'private';
    const timestamp = new Date(Date.UTC(2026, 7, 23, 10, number)).toISOString();
    return {
      id: `cwf_random_${String(number).padStart(3, '0')}`,
      slug: `random-chat-${String(number).padStart(3, '0')}`,
      name: `${adjective} ${noun} ${String(number).padStart(3, '0')}${number % 10 === 0 ? ' Ω 🚀' : ''}`,
      description: `Randomized private chat ${number}; output ${output}; punctuation !@#$%^&*().`,
      source: number % 3 === 0 ? 'generated' : number % 3 === 1 ? 'imported' : 'existing',
      visibility: 'private',
      status,
      source_workflow_name: number % 3 === 2 ? 'workflow-001' : null,
      output_compatibility: {
        supported: true,
        detected_types: [output],
        fallback_to_text: output !== 'text',
        warnings: [],
      },
      created_at: timestamp,
      updated_at: timestamp,
    };
  });
}

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function installChatApi(page: Page) {
  let chats = randomChats();
  let runNumber = 0;
  const runBodies: Array<Record<string, unknown>> = [];
  const requestCounts = new Map<string, number>();
  const runToChat = new Map<string, string>();
  const conversations = new Map<string, { id: string; messages: Array<Record<string, unknown>> }>();

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/auth/')) {
      await route.continue();
      return;
    }
    requestCounts.set(url.pathname, (requestCounts.get(url.pathname) ?? 0) + 1);

    if (url.pathname === '/auth/me') return json(route, { username: 'chat-stress-user' });
    if (url.pathname === '/api/chat-workspace/experiences') return json(route, { experiences: [] });
    if (url.pathname === '/api/workflows/chat-catalog') return json(route, []);
    if (url.pathname === '/api/chat-workflows' && request.method() === 'GET') {
      return json(route, { workflows: chats });
    }
    if (url.pathname.endsWith('/request-publication') && request.method() === 'POST') {
      const id = url.pathname.split('/').at(-2)!;
      chats = chats.map(chat => chat.id === id ? { ...chat, status: 'publish_requested' } : chat);
      return json(route, chats.find(chat => chat.id === id));
    }
    if (/^\/api\/chat-workflows\/[^/]+$/.test(url.pathname) && request.method() === 'DELETE') {
      const id = decodeURIComponent(url.pathname.split('/').pop()!);
      chats = chats.filter(chat => chat.id !== id);
      return json(route, { id, archived: true });
    }
    if (/^\/api\/chat-workflows\/[^/]+$/.test(url.pathname) && request.method() === 'GET') {
      const id = decodeURIComponent(url.pathname.split('/').pop()!);
      const chat = chats.find(item => item.id === id);
      return chat ? json(route, { ...chat, yaml: chatYaml }) : json(route, { detail: 'Private Chat workflow not found' }, 404);
    }
    if (url.pathname === '/api/chat-conversations/resolve' && request.method() === 'POST') {
      const body = request.postDataJSON() as { workflow_source: string; workflow_id: string };
      const key = `${body.workflow_source}:${body.workflow_id}`;
      const existing = conversations.get(key) ?? {
        id: `conversation-${body.workflow_id}`,
        messages: [],
      };
      conversations.set(key, existing);
      return json(route, {
        conversation: {
          id: existing.id, workflow_source: body.workflow_source, workflow_id: body.workflow_id,
          created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
        },
        messages: existing.messages,
      });
    }
    if (/^\/api\/chat-conversations\/[^/]+\/messages$/.test(url.pathname) && request.method() === 'POST') {
      const conversationId = decodeURIComponent(url.pathname.split('/').at(-2)!);
      const conversation = [...conversations.values()].find(item => item.id === conversationId);
      if (!conversation) return json(route, { detail: 'Chat conversation not found' }, 404);
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = conversation.messages.find(message => message.id === body.message_id);
      if (existing) return json(route, existing, 201);
      const message = {
        id: body.message_id, role: body.role, content: body.content, run_id: body.run_id ?? null,
        created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      };
      conversation.messages.push(message);
      return json(route, message, 201);
    }
    if (url.pathname === '/api/llm/models') {
      return json(route, { models: [
        {
          name: 'gpt-5-mini', display_name: 'GPT-5 Mini', provider: 'openai', local: false,
          enabled: true, configured: true, tool_calling: true, structured_output: true,
          reasoning_efforts: [], platform_modalities: ['text'],
        },
        {
          name: 'claude-haiku-4-5', display_name: 'Claude Haiku 4.5', provider: 'anthropic', local: false,
          enabled: true, configured: true, tool_calling: true, structured_output: true,
          reasoning_efforts: [], platform_modalities: ['text'],
        },
      ] });
    }
    if (url.pathname === '/api/prompt-templates') {
      return json(route, { templates: [{
        id: 'template-1', title: 'Executive comparison', description: 'Compare two options',
        category: 'Compare', content: 'Compare {{option_a}} with {{option_b}} for an executive audience.',
        variables: ['option_a', 'option_b'], favorite: true, built_in: true,
        created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
      }] });
    }
    if (url.pathname === '/api/workflows/run' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      runBodies.push(body);
      runNumber += 1;
      const runId = String(body.run_id ?? `chat-stress-run-${runNumber}`);
      runToChat.set(runId, String(body.workflow_id ?? 'unknown'));
      return json(route, { run_id: runId, status: 'running' });
    }
    if (/^\/api\/runs\/[^/]+\/events$/.test(url.pathname)) {
      const runId = url.pathname.split('/').at(-2)!;
      return route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `id: 1\nevent: run_completed\ndata: {"type":"run_completed","run_id":"${runId}","event_id":1}\n\n`,
      });
    }
    if (/^\/api\/runs\/mine\/[^/]+$/.test(url.pathname)) {
      const runId = url.pathname.split('/').pop()!;
      const chatId = runToChat.get(runId) ?? 'unknown';
      return json(route, {
        run: {
          run_id: runId, session_id: 'chat-stress', workflow_name: chatId, status: 'completed',
          started_at: 1, ended_at: 2, duration_s: 1, node_count: 3, completed_node_count: 3,
          active_nodes: [], error: null, created_at: '2026-08-23T00:00:00Z',
          updated_at: '2026-08-23T00:00:01Z', inputs: {},
          outputs: { result: `Completed ${chatId}` }, node_runs: {},
        },
        audit: [],
      });
    }
    if (/^\/api\/runs\/mine\/[^/]+\/pending-gate$/.test(url.pathname)) {
      const runId = url.pathname.split('/').at(-2)!;
      return json(route, { run_id: runId, paused: false });
    }
    if (/^\/api\/runs\/mine\/[^/]+\/chat$/.test(url.pathname)) {
      return json(route, { turns: [], starter_questions: [] });
    }
    if (url.pathname === '/api/chat-workflows/presets/deep-research') {
      return json(route, chats[0]);
    }
    return json(route, { detail: `Unhandled chat-stress route ${url.pathname}` }, 404);
  });

  return {
    chats: () => chats,
    runBodies,
    requestCounts,
  };
}

function runtimeErrors(page: Page) {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(`pageerror: ${error.message}`));
  page.on('console', message => {
    if (message.type() === 'error') errors.push(`console: ${message.text()}`);
  });
  return errors;
}

test('loads and searches 100 randomized private chats', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  await page.goto('/chat');

  await expect(page.getByText('My workflows')).toBeVisible();
  await expect(page.getByText('Private', { exact: true })).toHaveCount(100);
  expect(state.chats()).toHaveLength(100);

  const target = state.chats()[73];
  const search = page.getByPlaceholder('Search workflows…');
  await search.fill(target.slug);
  await expect(page.getByRole('button', { name: new RegExp(target.name) })).toBeVisible();
  await search.fill('Ω 🚀');
  await expect(page.getByText('Private', { exact: true })).toHaveCount(10);
  await search.fill('no-random-chat-exists');
  await expect(page.getByText('No workflows match your search.')).toBeVisible();
  await search.fill('');

  expect(state.requestCounts.get('/api/chat-workflows')).toBeLessThanOrEqual(2);
  expect(errors).toEqual([]);
});

test('rapidly switches among 25 chats without cross-chat identity contamination', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  await page.goto('/chat');

  const sampled = state.chats().filter((_, index) => index % 4 === 0).slice(0, 25);
  for (const chat of sampled) {
    await page.goto(`/chat/private/${chat.id}`);
    await expect(page.getByText(`🔒 ${chat.name} · Private`, { exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Random QA Chat' })).toBeVisible();
  }

  const detailRequests = [...state.requestCounts.entries()]
    .filter(([pathname]) => /^\/api\/chat-workflows\/cwf_random_/.test(pathname))
    .reduce((total, [, count]) => total + count, 0);
  expect(detailRequests).toBeLessThanOrEqual(50);
  expect(errors).toEqual([]);
});

test('uses templates, formatting, style, model, context, and message execution', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  const chat = state.chats()[41];
  await page.goto(`/chat/private/${chat.id}`);

  await expect(page.getByRole('heading', { name: 'Workflow context' })).toBeVisible();
  await page.getByRole('button', { name: 'Collapse', exact: true }).click();
  await page.getByRole('button', { name: 'Workflow', exact: true }).click();
  await page.getByLabel('Response format').selectOption('table');
  await page.getByLabel('Writing style').selectOption('executive');
  await page.getByLabel('Model for Transform agents').selectOption('claude-haiku-4-5');

  await page.getByRole('button', { name: 'Templates' }).click();
  await page.getByRole('button', { name: /Executive comparison/ }).click();
  await page.getByLabel('option a').fill('Option Alpha');
  await page.getByLabel('option b').fill('Option Beta');
  await page.getByRole('button', { name: 'Insert into chat' }).click();

  const composer = page.getByPlaceholder('Ask a question…');
  await expect(composer).toHaveValue('Compare Option Alpha with Option Beta for an executive audience.');
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.getByText('Compare Option Alpha with Option Beta for an executive audience.')).toBeVisible();
  await expect(page.getByText(`Completed ${chat.id}`)).toBeVisible();

  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    origin: 'chat_saved_workflow',
    history_visibility: 'conversation_only',
    workflow_id: chat.id,
  });
  expect(String((state.runBodies[0].inputs as Record<string, unknown>).message)).toContain('table');
  expect(String(state.runBodies[0].workflow_yaml)).toContain('claude-haiku-4-5');
  expect(errors).toEqual([]);
});

test('prevents duplicate execution when Send is triggered ten times', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  await page.goto(`/chat/private/${state.chats()[9].id}`);
  await page.getByPlaceholder('Ask a question…').fill('Only execute once');
  const send = page.getByRole('button', { name: /Send/ });
  await send.evaluate((button: HTMLButtonElement) => {
    for (let index = 0; index < 10; index += 1) button.click();
  });
  await expect.poll(() => state.runBodies.length).toBe(1);
  expect(errors).toEqual([]);
});

test('restores the durable transcript after refresh without duplicating the run result', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  const chat = state.chats()[33];
  await page.goto(`/chat/private/${chat.id}`);
  await page.getByPlaceholder('Ask a question…').fill('Refresh persistence Ω 🚀');
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.getByText('Refresh persistence Ω 🚀')).toBeVisible();
  await expect(page.getByText(`Completed ${chat.id}`)).toBeVisible();
  await page.reload();
  await expect(page.getByText('Refresh persistence Ω 🚀')).toBeVisible();
  await expect(page.getByText(`Completed ${chat.id}`)).toHaveCount(1);
  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    workflow_id: chat.id,
    conversation_id: `conversation-${chat.id}`,
  });
  expect(errors).toEqual([]);
});

test('archive and publication actions update only the intended chat', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  await page.goto('/chat');

  const publishTarget = state.chats().find(chat => chat.status === 'private')!;
  const search = page.getByPlaceholder('Search workflows…');
  await search.fill(publishTarget.slug);
  await page.getByRole('button', { name: 'Request publication' }).click();
  await expect(page.getByText('Publication requested')).toBeVisible();
  expect(state.chats().find(chat => chat.id === publishTarget.id)?.status).toBe('publish_requested');

  await page.getByRole('button', { name: 'Archive' }).click();
  await expect(page.getByText('No workflows match your search.')).toBeVisible();
  expect(state.chats()).toHaveLength(99);
  expect(state.chats().some(chat => chat.id === publishTarget.id)).toBe(false);
  expect(errors).toEqual([]);
});

test('browser back and forward preserve the selected chat identity', async ({ page }) => {
  const errors = runtimeErrors(page);
  const state = await installChatApi(page);
  const first = state.chats()[2];
  const second = state.chats()[87];
  await page.goto(`/chat/private/${first.id}`);
  await page.goto(`/chat/private/${second.id}`);
  await expect(page.getByText(`🔒 ${second.name} · Private`, { exact: true })).toBeVisible();
  await page.goBack();
  await expect(page.getByText(`🔒 ${first.name} · Private`, { exact: true })).toBeVisible();
  await page.goForward();
  await expect(page.getByText(`🔒 ${second.name} · Private`, { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});