import { expect, test, type Page, type Route } from '@playwright/test';
import fs from 'node:fs/promises';
import path from 'node:path';


const WORKFLOW_NAME = 'chat_knowledge_five_models_image';
const WORKFLOW_PATH = path.resolve('../workflows/test_fixtures/chat_knowledge_five_models_image.yaml');
const DICTATED_QUESTION = 'What is the approved verification code?';
const ANSWER = 'The approved verification code is KS-4827 [1].';
const IMAGE_KEY = 'workflows/chat-multimodal-run/images/visual.png';
const MODELS = [
  'gpt-5-mini',
  'claude-sonnet-4-5',
  'claude-opus-5',
  'gpt-5.6-luna',
  'openrouter/anthropic/claude-sonnet-4.5',
];


async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}


function nodeRun(nodeId: string, typeName: string, output: unknown) {
  return {
    node_id: nodeId,
    type_name: typeName,
    status: 'completed',
    input: {},
    output,
    started_at: 1,
    ended_at: 2,
    duration_s: 1,
    error: null,
  };
}


async function installSpeech(page: Page) {
  await page.addInitScript((transcript: string) => {
    class Recognition {
      continuous = false;
      interimResults = false;
      lang = 'en-US';
      onresult: ((event: unknown) => void) | null = null;
      onerror: ((event: unknown) => void) | null = null;
      onend: (() => void) | null = null;
      start() {
        window.setTimeout(() => {
          this.onresult?.({
            resultIndex: 0,
            results: [{ isFinal: true, 0: { transcript } }],
          });
          this.onend?.();
        }, 0);
      }
      stop() { this.onend?.(); }
      abort() { this.onend?.(); }
    }
    class Utterance {
      rate = 1;
      pitch = 1;
      constructor(public text: string) {}
    }
    const browserWindow = window as typeof window & {
      SpeechRecognition: typeof Recognition;
      SpeechSynthesisUtterance: typeof Utterance;
      __spoken: string[];
    };
    browserWindow.SpeechRecognition = Recognition;
    browserWindow.SpeechSynthesisUtterance = Utterance;
    browserWindow.__spoken = [];
    Object.defineProperty(browserWindow, 'speechSynthesis', {
      configurable: true,
      value: {
        cancel() {},
        speak(value: { text: string }) { browserWindow.__spoken.push(value.text); },
      },
    });
  }, DICTATED_QUESTION);
}


async function installApi(page: Page, workflowYaml: string) {
  const runBodies: Array<Record<string, unknown>> = [];
  const transcript: Array<Record<string, unknown>> = [];
  const conversation = {
    id: 'conversation-multimodal',
    workflow_source: 'shared',
    workflow_id: WORKFLOW_NAME,
    created_at: '2026-08-23T00:00:00Z',
    updated_at: '2026-08-23T00:00:00Z',
  };
  let runId = 'chat-multimodal-run';

  const knowledgeOutput = {
    retrieved_chunks: [{
      chunk_id: 'chunk-policy-1',
      display_number: 1,
      document_id: 'doc-policy',
      source_version_id: 'source-v1',
      doc_title: 'Operations Handbook.pdf',
      text: 'The approved verification code is KS-4827.',
      page: 4,
      section: 'Verification',
      metadata: { source_uri: 'knowledge://operations-handbook' },
    }],
    citations: [{
      document_id: 'doc-policy',
      source_version_id: 'source-v1',
      chunk_id: 'chunk-policy-1',
      filename: 'Operations Handbook.pdf',
      page: 4,
      section: 'Verification',
      evidence_status: 'retrieved_not_verified',
    }],
    context: 'The approved verification code is KS-4827.',
    retrieval_trace_id: 'retrieval-1',
    collection_id: 'test-knowledge-source',
    resolved_index_id: 'test-index',
    retrieval_profile_id: 'test-retrieval-profile',
    candidate_count: 1,
    context_count: 1,
    timings_ms: { total_ms: 4 },
    resolved_resources: { index_id: 'test-index' },
    status: 'success',
  };
  const visualOutput = {
    generated: true,
    provider: 'openrouter',
    model: 'google/gemini-3.1-flash-image',
    minio_key: IMAGE_KEY,
    content_type: 'image/png',
    byte_size: 17,
    revised_prompt: null,
  };
  const nodeRuns = {
    start: nodeRun('start', 'StartAgent', { message: DICTATED_QUESTION, attachments: [] }),
    knowledge: nodeRun('knowledge', 'KnowledgeRetrieval', knowledgeOutput),
    model_one: nodeRun('model_one', 'TransformAgent', { raw: '{}', parsed: { result: 'Extracted KS-4827' }, status: 'ok', data: {}, defaulted: [] }),
    model_two: nodeRun('model_two', 'TransformAgent', { raw: '{}', parsed: { result: 'Verified KS-4827' }, status: 'ok', data: {}, defaulted: [] }),
    model_three: nodeRun('model_three', 'TransformAgent', { raw: '{}', parsed: { result: 'Use the approved code' }, status: 'ok', data: {}, defaulted: [] }),
    model_four: nodeRun('model_four', 'TransformAgent', { raw: '{}', parsed: { result: 'Answer with citation' }, status: 'ok', data: {}, defaulted: [] }),
    model_five: nodeRun('model_five', 'TransformAgent', { raw: '{}', parsed: { answer: ANSWER, image_prompt: 'Shield with KS-4827' }, status: 'ok', data: {}, defaulted: [] }),
    visual: nodeRun('visual', 'OpenAIImageGenerationAgent', visualOutput),
    reply: nodeRun('reply', 'EndAgent', { chat_message: ANSWER, result: { outcome: 'answered', message: ANSWER } }),
  };
  const nodeTypes = Object.fromEntries(Object.entries(nodeRuns).map(([id, value]) => [id, value.type_name]));

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/auth/')) {
      await route.continue();
      return;
    }
    if (url.pathname === '/auth/me') return json(route, { username: 'multimodal-user' });
    if (url.pathname === `/api/workflows/by-name/${WORKFLOW_NAME}`) {
      return json(route, { name: WORKFLOW_NAME, yaml: workflowYaml });
    }
    if (url.pathname === '/api/chat-conversations/resolve') {
      return json(route, { conversation, messages: transcript });
    }
    if (url.pathname === `/api/chat-conversations/${conversation.id}/messages` && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = transcript.find(message => message.id === body.message_id);
      if (existing) return json(route, existing, 201);
      const message = {
        id: body.message_id,
        role: body.role,
        content: body.content,
        run_id: body.run_id ?? null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      transcript.push(message);
      return json(route, message, 201);
    }
    if (url.pathname === '/api/llm/models') {
      return json(route, { models: MODELS.map(name => ({
        name,
        display_name: name,
        provider: name.startsWith('openrouter/') ? 'openrouter' : name.startsWith('claude') ? 'anthropic' : 'openai',
        local: false,
        enabled: true,
        configured: true,
        automatic: false,
        tool_calling: true,
        structured_output: true,
        reasoning_efforts: [],
        platform_modalities: ['text'],
      })) });
    }
    if (url.pathname === '/api/workflows/run' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      runBodies.push(body);
      runId = String(body.run_id ?? runId);
      return json(route, { run_id: runId, status: 'running' });
    }
    if (url.pathname === `/api/runs/${runId}/events`) {
      const events = [
        ...Object.keys(nodeRuns).map((nodeId, index) => (
          `id: ${index + 1}\nevent: node_completed\ndata: ${JSON.stringify({
            type: 'node_completed', run_id: runId, node_id: nodeId,
            output_preview: 'completed', ts: '2026-08-23T00:00:00Z', event_id: index + 1,
          })}\n\n`
        )),
        `id: 10\nevent: run_completed\ndata: ${JSON.stringify({
          type: 'run_completed', run_id: runId, ts: '2026-08-23T00:00:01Z', event_id: 10,
        })}\n\n`,
      ].join('');
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events });
    }
    if (url.pathname === `/api/runs/mine/${runId}`) {
      return json(route, {
        run: {
          run_id: runId,
          session_id: 'multimodal-user',
          workflow_name: WORKFLOW_NAME,
          status: 'completed',
          started_at: 1,
          ended_at: 2,
          duration_s: 1,
          node_count: 9,
          completed_node_count: 9,
          active_nodes: [],
          error: null,
          created_at: '2026-08-23T00:00:00Z',
          updated_at: '2026-08-23T00:00:01Z',
          inputs: { message: DICTATED_QUESTION },
          outputs: { outcome: 'answered', message: ANSWER, handoff: { image: IMAGE_KEY } },
          node_runs: nodeRuns,
          node_types: nodeTypes,
        },
        audit: [],
      });
    }
    if (url.pathname === `/api/runs/mine/${runId}/pending-gate`) {
      return json(route, { run_id: runId, paused: false });
    }
    if (url.pathname === '/api/files') {
      const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl2nKAAAAAASUVORK5CYII=', 'base64');
      return route.fulfill({ status: 200, contentType: 'image/png', body: png });
    }
    return json(route, { detail: `Unhandled multimodal route ${url.pathname}` }, 404);
  });

  return { runBodies, transcript };
}


test('dictated chat runs Knowledge Retrieval, five models and image generation, then restores output', async ({ page }) => {
  const workflowYaml = await fs.readFile(WORKFLOW_PATH, 'utf-8');
  await installSpeech(page);
  const state = await installApi(page, workflowYaml);

  await page.goto(`/chat/shared/${WORKFLOW_NAME}`);
  const composer = page.getByPlaceholder('Ask a question…');
  await expect(composer).toBeVisible();
  await page.getByRole('button', { name: '🎙 Dictate' }).click();
  await expect(composer).toHaveValue(DICTATED_QUESTION);
  await page.getByRole('button', { name: /Send/ }).click();

  const userMessage = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: DICTATED_QUESTION }).first();
  await expect(userMessage).toBeVisible();
  const assistant = page.locator('div.flex.justify-start:visible').filter({ hasText: ANSWER }).last();
  await expect(assistant.locator('p.whitespace-pre-wrap').filter({ hasText: ANSWER })).toBeVisible();
  await expect(assistant.locator('summary').filter({ hasText: 'Operations Handbook.pdf' })).toBeVisible();
  await expect(assistant.getByRole('img', { name: 'visual.png' })).toHaveAttribute(
    'src', /chat-multimodal-run%2Fimages%2Fvisual\.png/,
  );
  for (const label of [
    'Model 1 · Extract', 'Model 2 · Verify', 'Model 3 · Analyze',
    'Model 4 · Outline', 'Model 5 · Finalize',
  ]) {
    await expect(page.getByRole('button', { name: `Inspect ${label}` })).toHaveCount(1);
  }
  await expect(page.getByRole('button', { name: 'Inspect Knowledge Source' })).toHaveCount(1);
  await expect(page.getByRole('button', { name: 'Inspect Generate Answer Image' })).toHaveCount(1);

  await assistant.getByRole('button', { name: '🔊 Read aloud' })
    .evaluate((button: HTMLButtonElement) => button.click());
  await expect.poll(() => page.evaluate(() => (
    ((window as typeof window & { __spoken?: string[] }).__spoken ?? []).join('\n')
  ))).toContain(ANSWER);

  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    origin: 'chat_saved_workflow',
    history_visibility: 'conversation_only',
    workflow_id: WORKFLOW_NAME,
    conversation_id: 'conversation-multimodal',
    inputs: { message: expect.stringContaining(DICTATED_QUESTION) },
  });
  for (const model of MODELS) expect(String(state.runBodies[0].workflow_yaml)).toContain(model);

  await page.reload();
  const restoredUserMessage = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: DICTATED_QUESTION }).first();
  await expect(restoredUserMessage).toBeVisible();
  const restoredAssistant = page.locator('div.flex.justify-start:visible').filter({ hasText: ANSWER }).last();
  await expect(restoredAssistant.locator('p.whitespace-pre-wrap').filter({ hasText: ANSWER })).toBeVisible();
  await expect(restoredAssistant.locator('summary').filter({ hasText: 'Operations Handbook.pdf' })).toBeVisible();
  await expect(restoredAssistant.getByRole('img', { name: 'visual.png' })).toHaveAttribute(
    'src', /chat-multimodal-run%2Fimages%2Fvisual\.png/,
  );
  expect(state.runBodies).toHaveLength(1);
});