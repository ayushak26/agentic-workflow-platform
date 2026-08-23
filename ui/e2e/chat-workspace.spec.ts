import { expect, test, type Page, type Route } from '@playwright/test';


const OBJECTIVE = 'Explain recursion in simple language.';
const WORKFLOW_ID = 'cwf_workspace_1';
const SKILL_WORKFLOW_ID = 'cwf_skill_1';
const WEB_WORKFLOW_ID = 'cwf_web_1';
const RAG_WORKFLOW_ID = 'cwf_rag_1';
const DIAGRAM_WORKFLOW_ID = 'cwf_diagram_1';
const ANSWER = 'Recursion is when a process solves a problem by calling a smaller version of itself.';
const AMSTERDAM_ANSWER = 'Amsterdam is the capital of the Netherlands. In late August, daytime temperatures are generally around 20°C.';
const WEATHER_ANSWER = 'Amsterdam is currently 18°C with light rain and a moderate southwest wind. [1]';
const NO_KNOWLEDGE_ANSWER = 'No supporting information was found in the selected knowledge collection.';
const UNGROUNDED_ANSWER = 'Industrial pumps include centrifugal, positive-displacement, diaphragm, and submersible designs.';
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


async function installApi(page: Page, options: { failRetrievalOnce?: boolean; exposeExistingWorkflow?: boolean; exposeCollection?: boolean; exposeRagAgent?: boolean; uploadDocument?: boolean; hitl?: boolean; builderWorkflow?: boolean } = {}) {
  const runBodies: Array<Record<string, unknown>> = [];
  const retryBodies: Array<Record<string, unknown>> = [];
  const resumeBodies: Array<Record<string, unknown>> = [];
  const askBodies: Array<Record<string, unknown>> = [];
  const prepareBodies: Array<Record<string, unknown>> = [];
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
  const skillYaml = `name: Literature Review Skill
description: Approved scientific skill adapter
version: '1.0'
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config: { mode: chatbot, chatbot_name: Literature Review Skill, welcome_message: Ask a question. }
  - id: apply_skill
    type: ScientificSkillAgent
    config:
      objective: '{{outputs.start.message}}'
      skills: [literature-review]
      auto_select: false
  - id: reply
    type: EndAgent
    config: { mode: chat_response, chat_message: '${ANSWER}' }
edges:
  - from: start
    to: apply_skill
  - from: apply_skill
    to: reply
`;
  const webYaml = `name: Web Research Assistant
description: Current public web information
version: '1.0'
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config: { mode: chatbot, chatbot_name: Web Research Assistant, welcome_message: Ask a current-information question. }
  - id: search
    type: WebSearchAgent
    config: { query: '{{outputs.start.message}}', provider: auto, top_k: 8 }
  - id: answer
    type: TransformAgent
    config: { mode: ai, prompt_template: '{{outputs.search.results}}' }
  - id: reply
    type: EndAgent
    config: { mode: chat_response, chat_message: '${WEATHER_ANSWER}' }
edges:
  - from: start
    to: search
  - from: search
    to: answer
  - from: answer
    to: reply
`;
  const ragYaml = `name: Pump Knowledge Assistant
description: Grounded answers from the saved RAG Agent
version: '1.0'
inputs:
  conversation_summary:
    type: text
    required: false
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config: { mode: chatbot, chatbot_name: Pump Knowledge Assistant, welcome_message: Ask about the selected collection. }
  - id: rewrite_query
    type: TransformAgent
    config:
      mode: ai
      model: gpt-5
      input_fields:
        - { name: current_question, type: string, value: '{{outputs.start.message}}' }
        - { name: recent_conversation, type: string, value: '{{inputs.conversation_summary}}' }
      instructions: Return one standalone retrieval query.
      output_fields: [{ name: retrieval_query, type: text, required: true }]
  - id: rag
    type: RAGAgent
    config: { rag_agent_id: rag-pump, query: '{{outputs.rewrite_query.parsed.retrieval_query}}' }
  - id: reply
    type: EndAgent
    config: { mode: chat_response, chat_message: '${ANSWER}' }
edges:
  - from: start
    to: rewrite_query
  - from: rewrite_query
    to: rag
  - from: rag
    to: reply
`;
  const diagramYaml = `name: Architecture Diagram
description: Grounded architecture explanation and image
version: '1.0'
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config: { mode: chatbot, chatbot_name: Architecture Diagram, welcome_message: Attach source material. }
  - id: load_files
    type: WorkflowFileLoader
    config: { files: '{{outputs.start.attachments}}', fail_on_unreadable: false }
  - id: diagram_plan
    type: TransformAgent
    config: { mode: ai, prompt_template: '{{outputs.load_files.text}}' }
  - id: generate_image
    type: OpenAIImageGenerationAgent
    config: { prompt: '{{outputs.diagram_plan.parsed.image_prompt}}', output_format: png }
  - id: reply
    type: EndAgent
    config: { mode: chat_response, chat_message: '${ANSWER}' }
edges:
  - { from: start, to: load_files }
  - { from: load_files, to: diagram_plan }
  - { from: diagram_plan, to: generate_image }
  - { from: generate_image, to: reply }
`;
  const builderAdapterYaml = `name: Email Intake Chat
description: Universal Chat adapter
version: '1.0'
entry: start
exit: reply
nodes:
  - id: start
    type: StartAgent
    config: { mode: chatbot, chatbot_name: Email Intake, welcome_message: Describe the email. }
  - id: prepare_inputs
    type: TransformAgent
    config:
      mode: ai
      model: gpt-5
      prompt_template: '{{outputs.start.message}}'
      output_schema: { email_text: str, source_file: str, processed_at: str }
  - id: run_workflow
    type: SubprocessAgent
    config:
      workflow: verder_email_intake
      inputs:
        email_text: '{{outputs.prepare_inputs.parsed.email_text}}'
        source_file: '{{outputs.prepare_inputs.parsed.source_file}}'
        processed_at: '{{outputs.prepare_inputs.parsed.processed_at}}'
      result_from: workflow_output
  - id: answer
    type: TransformAgent
    config: { mode: ai, model: gpt-5, prompt_template: '{{outputs.run_workflow.result}}', output_schema: { answer: str } }
  - id: reply
    type: EndAgent
    config:
      mode: chat_response
      chat_message: '${ANSWER}'
      handoff:
        structured_result: '{{outputs.run_workflow.result}}'
edges:
  - { from: start, to: prepare_inputs }
  - { from: prepare_inputs, to: run_workflow }
  - { from: run_workflow, to: answer }
  - { from: answer, to: reply }
`;
  const workflow = {
    id: WORKFLOW_ID, slug: 'workspace-test', name: 'AI Workspace', description: 'Lightweight adapter',
    source: 'imported', visibility: 'private', status: 'private', source_workflow_name: null,
    output_compatibility: { supported: true, detected_types: ['text'], fallback_to_text: false, warnings: [] },
    created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
  };
  let runId = 'workspace-run';
  let retrievalFailed = false;
  let hitlPhase = options.hitl ? 1 : 0;

  await page.route('**/*', async route => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.startsWith('/api/') && !url.pathname.startsWith('/auth/')) return route.continue();
    if (url.pathname === '/auth/me') return json(route, { username: 'workspace-user' });
    if (url.pathname === '/api/knowledge/collections') return json(route, options.exposeCollection ? [{
      collection_id: 'pump-collection', name: 'Pump ICP2 Collection', description: 'Pump manuals', status: 'ready',
      document_count: 2, chunk_count: 12, active_index_id: 'pump-index', metadata_schema: {}, doc_types: ['technical_documentation'],
    }] : []);
    if (url.pathname === '/api/knowledge/collections/pump-collection') return json(route, {
      collection_id: 'pump-collection', name: 'Pump ICP2 Collection', description: 'Pump manuals', status: 'ready',
      document_count: 2, chunk_count: 12, active_index_id: 'pump-index', metadata_schema: {}, doc_types: ['technical_documentation'],
    });
    if (url.pathname === '/api/knowledge/collections/pump-collection/documents') return json(route, [{
      document_id: 'pump-manual', collection_id: 'pump-collection', filename: 'Pump Manual.pdf', mime_type: 'application/pdf', source_format: 'pdf', status: 'ready', error: null,
    }, {
      document_id: 'pump-spec', collection_id: 'pump-collection', filename: 'Pump Specification.docx', mime_type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', source_format: 'docx', status: 'ready', error: null,
    }]);
    if (url.pathname === '/api/rag-agents') return json(route, options.exposeRagAgent ? [{
      rag_agent_id: 'rag-pump', name: 'Pump Knowledge Assistant', description: 'Grounded pump documentation answers.',
      collection_id: 'pump-collection', retrieval_profile_id: 'ret-pump', generation_profile_id: 'gen-pump', routing_profile_id: null, status: 'active',
    }] : []);
    if (url.pathname === '/api/rag-agents/rag-pump') return json(route, {
      rag_agent_id: 'rag-pump', name: 'Pump Knowledge Assistant', description: 'Grounded pump documentation answers.',
      collection_id: 'pump-collection', retrieval_profile_id: 'ret-pump', generation_profile_id: 'gen-pump', routing_profile_id: null, status: 'active',
    });
    if (url.pathname === '/api/workflows' && request.method() === 'GET') return json(route, [{
      name: 'customer_triage', description: 'Route customer requests', use_case: 'support', version: '1.0',
      node_count: 4, updated_at: '2026-08-23T00:00:00Z',
      library: { title: 'Customer triage', visibility_status: 'draft' },
      readiness: { level: 'ready', items: [] },
    }]);
    if (url.pathname === '/api/workflows/chat-catalog') return json(route, options.exposeExistingWorkflow ? [{
      name: 'shared_pump', description: 'Shared pump workflow', use_case: 'operations', version: '1.0', node_count: 3,
      updated_at: '2026-08-23T00:00:00Z', library: { title: 'Shared pump review', summary: 'Review shared pump records.', visibility_status: 'approved' }, readiness: { level: 'ready', items: [] },
    }] : []);
    if (url.pathname === '/api/chat-workflows' && request.method() === 'GET') return json(route, { workflows: options.exposeExistingWorkflow ? [{ ...workflow, name: 'Pump ICP2', description: 'Summarize pump documentation.' }] : [] });
    if (url.pathname === '/api/chat-workspace/experiences') return json(route, { experiences: EXPERIENCES.map(([id, title]) => ({
      id, title, examples: [], default_plan: 'files', existing_workflow: null, capabilities: [],
    })) });
    if (url.pathname === '/api/research/skills') return json(route, {
      skills: [{ name: 'literature-review', description: 'Review papers and synthesize scientific evidence.', version: '1.0', license: 'MIT' }],
      load_errors: {},
    });
    if (url.pathname === '/api/chat-workspace/plan' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      const groundedDiagram = body.has_attachments === true && /architecture/i.test(String(body.objective ?? ''));
      const currentWeather = /\b(weather|forecast)\b/i.test(String(body.objective ?? ''));
      return json(route, groundedDiagram
        ? { kind: 'artifact', title: 'Architecture Diagram', reason: 'The attached source grounds the requested diagram.', yaml: diagramYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['files', 'diagram', 'image'] }
        : currentWeather
        ? { kind: 'web', title: 'Web Research Assistant', reason: 'Current public information is required.', yaml: webYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['web', 'sources'] }
        : { kind: 'llm', title: 'AI Workspace', reason: 'A lightweight LLM workflow is sufficient.', yaml: llmYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['llm'] });
    }
    if (url.pathname === '/api/chat-workflows/presets/general' && request.method() === 'POST') return json(route, { ...workflow, name: 'General Chat', slug: 'general-chat' }, 201);
    if (url.pathname === '/api/chat-workspace/prepare' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      prepareBodies.push(body);
      if (body.skill_name === 'literature-review') return json(route, {
        plan: { kind: 'llm', title: 'Literature Review Skill', reason: 'Selected skill', yaml: skillYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['scientific_skill', 'literature-review'] },
        workflow: { ...workflow, id: SKILL_WORKFLOW_ID, name: 'Literature Review Skill', slug: 'workspace-skill' },
      }, 201);
      if (/\b(weather|forecast)\b/i.test(String(body.objective ?? ''))) return json(route, {
        plan: { kind: 'web', title: 'Web Research Assistant', reason: 'Current public information is required.', yaml: webYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['web', 'sources'] },
        workflow: { ...workflow, id: WEB_WORKFLOW_ID, name: 'Web Research Assistant', slug: 'workspace-web' },
      }, 201);
      if (body.rag_agent_id === 'rag-pump' && body.collection_id === 'pump-collection') return json(route, {
        plan: { kind: 'retrieval', title: 'Knowledge Assistant', reason: 'A saved RAG Agent is configured for this collection.', yaml: ragYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['rag', 'citations'] },
        workflow: { ...workflow, id: RAG_WORKFLOW_ID, name: 'Pump Knowledge Assistant', slug: 'workspace-rag' },
      }, 201);
      if (body.has_attachments === true && /architecture/i.test(String(body.objective ?? ''))) return json(route, {
        plan: { kind: 'artifact', title: 'Architecture Diagram', reason: 'The attached source grounds the requested diagram.', yaml: diagramYaml, existing_workflow: null, experience_id: null, missing_requirements: [], capabilities: ['files', 'diagram', 'image'] },
        workflow: { ...workflow, id: DIAGRAM_WORKFLOW_ID, name: 'Architecture Diagram', slug: 'workspace-diagram' },
      }, 201);
      return json(route, { detail: 'Chat must use a saved workflow' }, 409);
    }
    if (url.pathname === `/api/chat-workflows/${WORKFLOW_ID}`) return json(route, { ...workflow, yaml: llmYaml });
    if (url.pathname === `/api/chat-workflows/${SKILL_WORKFLOW_ID}`) return json(route, { ...workflow, id: SKILL_WORKFLOW_ID, name: 'Literature Review Skill', slug: 'workspace-skill', yaml: skillYaml });
    if (url.pathname === `/api/chat-workflows/${WEB_WORKFLOW_ID}`) return json(route, { ...workflow, id: WEB_WORKFLOW_ID, name: 'Web Research Assistant', slug: 'workspace-web', yaml: webYaml });
    if (url.pathname === `/api/chat-workflows/${RAG_WORKFLOW_ID}`) return json(route, { ...workflow, id: RAG_WORKFLOW_ID, name: 'Pump Knowledge Assistant', slug: 'workspace-rag', yaml: ragYaml });
    if (url.pathname === `/api/chat-workflows/${DIAGRAM_WORKFLOW_ID}`) return json(route, { ...workflow, id: DIAGRAM_WORKFLOW_ID, name: 'Architecture Diagram', slug: 'workspace-diagram', yaml: diagramYaml });
    if (url.pathname === '/api/chat-workflows/adapters/by-name/verder_email_intake') return json(route, { workflow_name: 'verder_email_intake', yaml: builderAdapterYaml, adapted: true });
    if (url.pathname === '/api/chat-conversations/resolve') return json(route, {
      conversation: {
        id: 'conversation-workspace', workflow_source: 'private', workflow_id: WORKFLOW_ID,
        created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:00Z',
      },
      messages,
    });
    if (url.pathname === '/api/llm/models') return json(route, { models: [] });
    if (url.pathname === '/api/workflow-input-files' && request.method() === 'POST') {
      return json(route, { files: [options.uploadDocument ? {
        kind: 'workflow_file', file_id: 'architecture-pdf', minio_key: 'chat/eurskem-architecture.pdf',
        name: 'eurskem-architecture.pdf', content_type: 'application/pdf', byte_size: 24, category: 'document',
      } : {
        kind: 'workflow_file', file_id: 'pasted-image', minio_key: 'chat/pasted-image.png',
        name: 'pasted-image.png', content_type: 'image/png', byte_size: 4, category: 'image',
      }] });
    }
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
    if (url.pathname.startsWith('/api/chat-conversations/conversation-workspace/messages/') && request.method() === 'PUT') {
      const id = decodeURIComponent(url.pathname.split('/').pop() ?? '');
      const body = request.postDataJSON() as Record<string, unknown>;
      const existing = messages.find(item => item.id === id);
      if (!existing) return json(route, { detail: 'message not found' }, 404);
      Object.assign(existing, { role: body.role, content: body.content, run_id: body.run_id ?? null, updated_at: '2026-08-23T00:00:02Z' });
      return json(route, existing);
    }
    if (url.pathname === '/api/workflows/run' && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      runBodies.push(body);
      runId = String(body.run_id ?? runId);
      retrievalFailed = options.failRetrievalOnce === true;
      return json(route, { run_id: runId, status: 'running' });
    }
    if (url.pathname === `/api/workflows/${runId}/resume` && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      resumeBodies.push(body);
      hitlPhase += 1;
      return json(route, { run_id: runId, status: hitlPhase === 2 ? 'paused' : 'completed' });
    }
    if (url.pathname.endsWith('/retry') && url.pathname.startsWith('/api/runs/mine/') && request.method() === 'POST') {
      const body = request.postDataJSON() as Record<string, unknown>;
      retryBodies.push(body);
      runId = String(body.run_id);
      retrievalFailed = false;
      return json(route, { run_id: runId, status: 'running', retry: { source_run_id: 'workspace-run', reused_node_count: 1 } });
    }
    if (url.pathname === `/api/runs/${runId}/events`) return route.fulfill({
      status: 200, contentType: 'text/event-stream',
       body: hitlPhase === 1 || hitlPhase === 2
         ? `id: 1\nevent: node_paused\ndata: {"type":"node_paused","run_id":"${runId}","node_id":"${hitlPhase === 1 ? 'review_one' : 'review_two'}","context":{},"event_id":1}\n\n`
         : retrievalFailed
        ? `id: 1\nevent: run_failed\ndata: {"type":"run_failed","run_id":"${runId}","event_id":1}\n\n`
        : `id: 1\nevent: run_completed\ndata: {"type":"run_completed","run_id":"${runId}","event_id":1}\n\n`,
    });
    if (url.pathname === `/api/runs/mine/${runId}`) return json(route, {
      run: {
         run_id: runId, session_id: 'workspace-user', workflow_name: 'AI Workspace', status: hitlPhase === 1 || hitlPhase === 2 ? 'paused' : retrievalFailed ? 'failed' : 'completed',
        started_at: 1, ended_at: 2, duration_s: 1, node_count: 3, completed_node_count: 3,
         active_nodes: [], pause_kind: hitlPhase === 1 || hitlPhase === 2 ? 'hitl_gate' : undefined, error: retrievalFailed ? 'RETRIEVAL_TIMEOUT: Knowledge retrieval exceeded its deadline.' : null,
        retry_available: retrievalFailed, attempt: retrievalFailed ? 1 : 2,
        created_at: '2026-08-23T00:00:00Z', updated_at: '2026-08-23T00:00:01Z',
        inputs: { message: OBJECTIVE }, outputs: options.builderWorkflow ? {
          outcome: 'answered', message: ANSWER,
          handoff: { structured_result: { source_file: 'Chat message', processed_at: '2026-08-23T22:00:00Z', extraction: { customer_name: 'Ada' } } },
        } : String(runBodies.at(-1)?.workflow_yaml ?? '').includes('rag_agent_id: rag-pump') ? {
          start: { data: {}, message: String((runBodies.at(-1)?.inputs as Record<string, unknown>)?.message ?? ''), attachments: [], missing: [] },
          rag: { query: String((runBodies.at(-1)?.inputs as Record<string, unknown>)?.message ?? ''), answer: NO_KNOWLEDGE_ANSWER, citations: [], sources: [], relevant_context: [], answering_model: 'auto', resolved_answering_model: 'auto', retrievals: [], grounding_for_drafter: {}, retrieval_trace_id: 'retreq-rag', collection_id: 'pump-collection', resolved_index_id: 'pump-index' },
          reply: { result: { outcome: 'answered', message: NO_KNOWLEDGE_ANSWER } },
        } : { message: String(runBodies.at(-1)?.workflow_yaml ?? '').includes('WebSearchAgent') ? WEATHER_ANSWER : runBodies.slice(0, -1).some(body => String(body.workflow_yaml ?? '').includes('rag_agent_id: rag-pump')) ? UNGROUNDED_ANSWER : runBodies.length > 1 ? AMSTERDAM_ANSWER : ANSWER },
        node_types: String(runBodies.at(-1)?.workflow_yaml ?? '').includes('rag_agent_id: rag-pump')
          ? { start: 'StartAgent', rag: 'RAGAgent', reply: 'EndAgent' }
          : { start: 'StartAgent', answer: 'TransformAgent', reply: 'EndAgent' },
        node_runs: String(runBodies.at(-1)?.workflow_yaml ?? '').includes('rag_agent_id: rag-pump') ? {
          rag: { output: { answer: NO_KNOWLEDGE_ANSWER, citations: [], sources: [], relevant_context: [], retrievals: [], retrieval_trace_id: 'retreq-rag', collection_id: 'pump-collection', resolved_index_id: 'pump-index' } },
          reply: { output: { result: { outcome: 'answered', message: NO_KNOWLEDGE_ANSWER } } },
        } : {
          answer: { status: 'completed', output: { parsed: { answer: String(runBodies.at(-1)?.workflow_yaml ?? '').includes('WebSearchAgent') ? WEATHER_ANSWER : runBodies.slice(0, -1).some(body => String(body.workflow_yaml ?? '').includes('rag_agent_id: rag-pump')) ? UNGROUNDED_ANSWER : runBodies.length > 1 ? AMSTERDAM_ANSWER : ANSWER } }, duration_s: 0.4 },
          reply: { output: { chat_message: String(runBodies.at(-1)?.workflow_yaml ?? '').includes('WebSearchAgent') ? WEATHER_ANSWER : runBodies.slice(0, -1).some(body => String(body.workflow_yaml ?? '').includes('rag_agent_id: rag-pump')) ? UNGROUNDED_ANSWER : runBodies.length > 1 ? AMSTERDAM_ANSWER : ANSWER } },
        },
      }, audit: [{
        run_id: runId, session_id: 'workspace-user', node_id: 'answer', event_type: 'node_start', actor: 'system', payload: {}, ts: '2026-08-23T00:00:00Z',
      }, {
        run_id: runId, session_id: 'workspace-user', node_id: 'answer', event_type: 'node_end', actor: 'system', payload: { answer: 'dict[1]' }, ts: '2026-08-23T00:00:01Z',
      }],
    });
    if (url.pathname === `/api/runs/mine/${runId}/chat` && request.method() === 'GET') return json(route, { turns: [], starter_questions: [] });
    if (url.pathname === `/api/runs/mine/${runId}/chat` && request.method() === 'POST') {
      askBodies.push(request.postDataJSON() as Record<string, unknown>);
      return json(route, { answer: 'I can turn the existing result into an executive presentation outline within this workflow conversation.' });
    }
    if (url.pathname === `/api/runs/mine/${runId}/pending-gate`) {
      if (hitlPhase === 1) return json(route, {
        gate_id: `${runId}:review_one:1`, run_id: runId, paused: true, pause_kind: 'hitl_gate', node_id: 'review_one',
        question: 'Approve the customer response?', review_purpose: 'Customer-facing text requires review.', context: { customer: 'Example GmbH' },
        allowed_actions: ['approve', 'reject'], content: null, panels: [{ label: 'Customer', field: 'customer', value: 'Example GmbH', available: true }],
        display_name: 'Customer Review', allow_document_override: false, max_edit_chars: 5000,
      });
      if (hitlPhase === 2) return json(route, {
        gate_id: `${runId}:review_two:2`, run_id: runId, paused: true, pause_kind: 'hitl_gate', node_id: 'review_two',
        question: 'Finalize the response?', review_purpose: 'Apply any final wording changes.', context: {},
        allowed_actions: ['approve', 'edit', 'reject'], content: { text: JSON.stringify({ answer: 'Original final response', confidence: 0.9 }), format: 'json', source: 'workflow' }, panels: [],
        display_name: 'Final Response Review', allow_document_override: true, max_edit_chars: 5000,
      });
      return json(route, { run_id: runId, paused: false });
    }
    return json(route, { detail: `Unhandled workspace route ${url.pathname}` }, 404);
  });
  return { runBodies, retryBodies, resumeBodies, askBodies, prepareBodies };
}


test('workflow-neutral chat selects the lightweight path and persists the result', async ({ page }) => {
  const state = await installApi(page);
  await page.goto('/chat');
  await expect(page.getByRole('heading', { name: 'What would you like to understand?' })).toBeVisible();
  if ((page.viewportSize()?.width ?? 1280) > 1180) {
    await expect(page.getByRole('complementary', { name: 'Sources panel' })).toBeVisible();
    await expect(page.getByRole('complementary', { name: 'Session panel' })).toBeVisible();
  } else {
    await expect(page.getByRole('navigation', { name: 'Chat workspace panels' })).toBeVisible();
  }
  await expect(page.getByText('No sources selected. Chat can still answer general questions.')).toBeVisible();
  await page.getByPlaceholder('Ask anything about your sources…').fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Send message' }).click();

  await expect(page).toHaveURL(new RegExp(`/chat/private/${WORKFLOW_ID}`));
  const composer = page.getByPlaceholder('Ask anything about your sources…');
  await expect(composer).toHaveValue(OBJECTIVE);
  await expect(page.getByLabel('Response format')).toHaveCount(0);
  await expect(page.getByLabel('Writing style')).toHaveCount(0);
  await expect(page.getByLabel('Model for Transform agents')).toHaveCount(0);
  await expect(page.getByText('Tools', { exact: true })).toHaveCount(0);
  await expect(page.getByText('MCP', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: /Send/ }).click();
  const assistant = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last();
  await expect(assistant).toBeVisible();
  await expect(page.getByText('Chat Input', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Prepare Answer', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Chat Reply', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'View workflow activity' })).toHaveCount(0);
  const activitySummary = page.getByRole('button', { name: /Completed workflow activity/ });
  await expect(activitySummary).toBeVisible();
  await activitySummary.click();
  const transcriptStep = page.locator('.chat-agent-activity-step[data-node-id="answer"]');
  await transcriptStep.click();
  await page.getByRole('tab', { name: 'Activity' }).click();
  await expect(page.getByRole('complementary', { name: 'Session panel' }).locator('.chat-session-event.is-selected')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Open technical execution' })).toBeVisible();
  await page.getByRole('tab', { name: 'Audit' }).click();
  await page.getByRole('button', { name: 'Activity', exact: true }).last().click();
  await expect(page.getByText('Node completed')).toBeVisible();
  await page.getByRole('tab', { name: 'Overview' }).click();
  await expect(page.getByRole('button', { name: 'Copy trace' })).toBeVisible();
  const traceDownload = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export trace' }).click();
  expect((await traceDownload).suggestedFilename()).toMatch(/-trace\.json$/);
  await expect(page.getByText('Raw:', { exact: false })).toHaveCount(0);
  await expect(page.getByText('Parsed:', { exact: false })).toHaveCount(0);
  await expect(page.getByText('Data: [structured value]', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Workflow context', { exact: true })).toHaveCount(0);

  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    origin: 'chat_saved_workflow', history_visibility: 'conversation_only', workflow_id: WORKFLOW_ID,
    inputs: { message: expect.stringContaining(OBJECTIVE) },
  });
  const localHistory = await page.evaluate(() => JSON.parse(window.localStorage.getItem('eurskem.chat.local-history.v1') ?? '{}'));
  expect(localHistory.chats[0]).toMatchObject({ title: OBJECTIVE, workflowId: WORKFLOW_ID, workflowSource: 'private' });
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


test('General Chat runs each unrelated follow-up as a new workflow turn in the same session', async ({ page }) => {
  const state = await installApi(page);
  await page.goto('/chat');
  await page.getByPlaceholder('Ask anything about your sources…').fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Send message' }).click();
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();
  await expect(page.getByRole('button', { name: 'Restart from beginning' })).toHaveCount(0);

  const followUp = 'Where is Amsterdam and generally what is the temperature during this time?';
  await page.getByPlaceholder('Ask anything about your sources…').fill(followUp);
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: AMSTERDAM_ANSWER }).last()).toBeVisible();

  expect(state.runBodies).toHaveLength(2);
  expect(state.askBodies).toHaveLength(0);
  expect(state.runBodies[0]).toMatchObject({
    origin: 'chat_saved_workflow', history_visibility: 'conversation_only', workflow_id: WORKFLOW_ID,
    conversation_id: 'conversation-workspace', inputs: { message: OBJECTIVE },
  });
  expect(state.runBodies[1]).toMatchObject({
    origin: 'chat_saved_workflow', history_visibility: 'conversation_only', workflow_id: WORKFLOW_ID,
    conversation_id: 'conversation-workspace', inputs: { message: followUp },
  });
  expect(state.runBodies[1].run_id).not.toBe(state.runBodies[0].run_id);
  expect(state.runBodies[1].message_id).not.toBe(state.runBodies[0].message_id);

  await page.reload();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: AMSTERDAM_ANSWER }).last()).toBeVisible();
});


test('General Chat routes current Amsterdam weather through web search', async ({ page }) => {
  const state = await installApi(page);
  const question = 'What is the current weather in Amsterdam?';
  await page.goto('/chat');
  await page.getByPlaceholder('Ask anything about your sources…').fill(question);
  await page.getByRole('button', { name: 'Send message' }).click();

  await expect(page).toHaveURL(new RegExp(`/chat/private/${WEB_WORKFLOW_ID}`));
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: WEATHER_ANSWER }).last()).toBeVisible();

  expect(state.prepareBodies).toEqual([{ objective: question }]);
  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    history_visibility: 'conversation_only', workflow_id: WEB_WORKFLOW_ID,
    conversation_id: 'conversation-workspace', inputs: { message: question },
  });
  expect(String(state.runBodies[0].workflow_yaml)).toContain('WebSearchAgent');
  expect(String(state.runBodies[0].workflow_yaml)).not.toContain('I can’t access live weather data');
});


test('source-first entry loads and executes an approved Skill', async ({ page }) => {
  const state = await installApi(page);
  await page.goto('/chat');

  for (const suggestion of [
    'Summarize the key findings', 'What are the biggest risks?', 'Compare these sources',
    'Find contradictions', 'Create an executive brief',
  ]) {
    await expect(page.getByRole('button', { name: suggestion })).toBeVisible();
  }
  await expect(page.getByLabel('Existing workflow')).toHaveCount(0);
  await expect(page.getByText('My workflows')).toHaveCount(0);
  await expect(page.getByPlaceholder('Search workflows…')).toHaveCount(0);
  await page.getByRole('button', { name: '@ Skill' }).click();
  const skillMenu = page.getByRole('menu', { name: 'Choose a skill' });
  const literatureReview = skillMenu.getByRole('menuitem', { name: /Literature Review/ });
  await expect(literatureReview).toBeVisible();
  await expect(literatureReview).toHaveClass(/without-icon/);
  const skillContent = literatureReview.locator('.chat-composer-menu-content');
  await expect(skillContent).toBeVisible();
  expect((await skillContent.boundingBox())?.width ?? 0).toBeGreaterThan(200);
  await literatureReview.click();
  await expect(page.getByRole('button', { name: '@ Literature Review' })).toBeVisible();
  await page.getByRole('button', { name: '/ Create' }).click();
  await expect(page.getByRole('menu', { name: 'Create something' }).getByRole('menuitem', { name: /Presentation/ })).toBeVisible();
  await page.getByRole('menu', { name: 'Create something' }).getByRole('menuitem', { name: /Presentation/ }).click();
  await expect(page.getByRole('dialog', { name: 'Create presentation' })).toBeVisible();
  await page.getByRole('button', { name: 'Close Create presentation' }).click();
  await page.getByRole('button', { name: 'Compare these sources' }).click();
  await expect(page.getByPlaceholder('Ask anything about your sources…')).toHaveValue('Compare these sources');
  await page.getByPlaceholder('Ask anything about your sources…').fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page).toHaveURL(new RegExp(`/chat/private/${SKILL_WORKFLOW_ID}`));
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();
  expect(state.prepareBodies).toEqual([{ objective: OBJECTIVE, skill_name: 'literature-review' }]);
  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    history_visibility: 'conversation_only', workflow_id: SKILL_WORKFLOW_ID,
  });
  expect(String(state.runBodies[0].workflow_yaml)).toContain('ScientificSkillAgent');
  expect(String(state.runBodies[0].workflow_yaml)).toContain('literature-review');
});


test('pasted URLs become explicit web sources and pasted images upload', async ({ page }) => {
  await installApi(page);
  await page.goto('/chat');
  const composer = page.getByPlaceholder('Ask anything about your sources…');

  await composer.evaluate((element) => {
    const data = new DataTransfer();
    data.setData('text/plain', 'Review https://example.com/research/report.');
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  });
  if ((page.viewportSize()?.width ?? 1280) <= 1180) {
    await page.getByRole('button', { name: 'Sources', exact: true }).click();
  }
  await expect(page.getByText('example.com/research/report', { exact: true })).toBeVisible();
  await expect(page.getByRole('complementary', { name: 'Sources panel' }).getByText('Web source · used with this request', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Show usage for example.com/research/report' }).click();
  await expect(page.getByRole('tab', { name: 'Sources' })).toHaveAttribute('aria-selected', 'true');
  await expect(page.getByRole('complementary', { name: 'Session panel' })).toContainText('example.com/research');
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Chat', exact: true }).click();

  const uploadRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname === '/api/workflow-input-files'
    && request.method() === 'POST'
  ));
  await composer.evaluate((element) => {
    const data = new DataTransfer();
    data.items.add(new File([new Uint8Array([137, 80, 78, 71])], 'pasted-image.png', { type: 'image/png' }));
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  });
  await uploadRequest;
  if ((page.viewportSize()?.width ?? 1280) <= 1180) {
    await page.getByRole('button', { name: 'Sources', exact: true }).click();
  }
  await expect(page.getByRole('complementary', { name: 'Sources panel' }).getByText('pasted-image.png', { exact: true })).toBeVisible();
  await expect(page.getByText('2 sources selected', { exact: true })).toBeVisible();
});


test('attached architecture source routes to grounded image generation', async ({ page }) => {
  const state = await installApi(page, { uploadDocument: true });
  await page.goto('/chat');
  const fileInput = page.locator('.chat-composer-action input[type="file"]');
  await fileInput.setInputFiles({
    name: 'eurskem-architecture.pdf', mimeType: 'application/pdf',
    buffer: Buffer.from('Eurskem architecture source'),
  });

  const question = 'Explain the architecture of Eurskem and generate image of it';
  await page.getByPlaceholder('Ask anything about your sources…').fill(question);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page).toHaveURL(new RegExp(`/chat/private/${DIAGRAM_WORKFLOW_ID}`));
  await expect(page.getByText('Choose an existing workflow that accepts attachments before sending files.')).toHaveCount(0);
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();

  expect(state.prepareBodies).toEqual([{
    objective: question, has_attachments: true, attachment_categories: ['document'],
  }]);
  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    history_visibility: 'conversation_only', workflow_id: DIAGRAM_WORKFLOW_ID,
    inputs: {
      message: question,
      attachments: [expect.objectContaining({ file_id: 'architecture-pdf', category: 'document' })],
    },
  });
  const submittedYaml = String(state.runBodies[0].workflow_yaml);
  expect(submittedYaml).toContain('WorkflowFileLoader');
  expect(submittedYaml).toContain('id: diagram_plan');
  expect(submittedYaml).toContain('OpenAIImageGenerationAgent');
});


test('browser-local chat history isolates and restores draft sources on desktop and mobile', async ({ page }) => {
  await installApi(page);
  await page.goto('/chat');
  await expect(page).toHaveURL(/\?chat=chat-/);
  const firstChat = new URL(page.url()).searchParams.get('chat');
  expect(firstChat).toBeTruthy();
  const composer = page.getByPlaceholder('Ask anything about your sources…');
  await composer.evaluate((element) => {
    const data = new DataTransfer();
    data.setData('text/plain', 'Review https://example.com/first-source.');
    element.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
  });
  if ((page.viewportSize()?.width ?? 1280) <= 1180) {
    await page.getByRole('button', { name: 'Sources', exact: true }).click();
  }
  await expect(page.getByText('example.com/first-source', { exact: true })).toBeVisible();

  await page.getByRole('button', { name: 'Chats', exact: true }).click();
  await expect(page.getByText(/Stored only in this browser/)).toBeVisible();
  await page.getByRole('button', { name: '＋ New chat' }).click();
  await expect(page.getByText('example.com/first-source', { exact: true })).toHaveCount(0);
  expect(new URL(page.url()).searchParams.get('chat')).not.toBe(firstChat);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Chats', exact: true }).click();
  const history = page.getByRole('dialog', { name: 'Chats' });
  await expect(history).toBeVisible();
  await history.getByRole('button', { name: /New chat.*Draft/ }).last().click();
  await expect(page).toHaveURL(new RegExp(`chat=${firstChat}`));
  if ((page.viewportSize()?.width ?? 1280) <= 1180) {
    await page.getByRole('button', { name: 'Sources', exact: true }).click();
  }
  await expect(page.getByText('example.com/first-source', { exact: true })).toBeVisible();
});


test('Knowledge retrieval timeout offers checkpoint-backed retry', async ({ page }) => {
  const state = await installApi(page, { failRetrievalOnce: true });
  await page.goto('/chat');
  await page.getByPlaceholder('Ask anything about your sources…').fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Send message' }).click();
  await page.getByRole('button', { name: /Send/ }).click();

  await expect(page.getByText('Knowledge search took too long')).toBeVisible();
  await expect(page.getByText(/Your sources and conversation are safe/)).toBeVisible();
  await expect(page.getByText('RETRIEVAL_TIMEOUT', { exact: false })).toBeHidden();
  await page.getByRole('button', { name: 'Retry Knowledge search' }).click();
  await expect.poll(() => state.retryBodies.length).toBe(1);
  expect(state.retryBodies[0].run_id).toBeTruthy();
  await expect(page.getByText(/retrying with completed steps/)).toBeVisible();
});


test('HITL reviews stay inline through sequential gates and survive refresh', async ({ page }) => {
  const state = await installApi(page, { hitl: true });
  await page.goto('/chat');
  await page.getByPlaceholder('Ask anything about your sources…').fill(OBJECTIVE);
  await page.getByRole('button', { name: 'Send message' }).click();
  await page.getByRole('button', { name: /Send/ }).click();

  await expect(page.getByText('Action required — Customer Review')).toBeVisible();
  await expect(page.getByText('No external action has been taken')).toBeVisible();
  await expect(page.getByText('Approve the customer response?')).toBeVisible();
  await expect(page.getByText('Example GmbH')).toBeVisible();
  await expect(page.getByText('Resolve the pending review to continue.')).toBeVisible();
  await page.getByRole('button', { name: 'Approve and continue' }).click();

  await expect(page.getByText('Review resolved — approve.')).toBeVisible();
  await expect(page.getByText('Action required — Final Response Review')).toBeVisible();
  const editor = page.getByLabel('Edit before continuing');
  await expect(editor).toHaveValue('Original final response');
  await editor.fill('Final approved response');
  await page.getByRole('button', { name: 'Save changes and continue' }).click();

  await expect(page.getByText('Review resolved — edit.')).toBeVisible();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();
  await expect(page.getByText('Resolve the pending review to continue.')).toHaveCount(0);
  expect(state.resumeBodies).toEqual([
    { decision: { decision: 'approve' } },
    { decision: { decision: 'edit', edited_content: expect.objectContaining({
      text: JSON.stringify({ answer: 'Final approved response', confidence: 0.9 }),
      format: 'json', source: 'editor', source_document: null,
    }) } },
  ]);

  await page.reload();
  await expect(page.getByText('Review resolved — approve.')).toBeVisible();
  await expect(page.getByText('Review resolved — edit.')).toBeVisible();
  await expect(page.getByText('Action required — Customer Review')).toHaveCount(0);
  await expect(page.getByText('Action required — Final Response Review')).toHaveCount(0);
});


test('Builder workflow Chat uses universal LLM adapters and preserves downloadable JSON', async ({ page }) => {
  const state = await installApi(page, { builderWorkflow: true });
  await page.goto('/chat/shared/verder_email_intake');
  const composer = page.getByPlaceholder('Ask anything about your sources…');
  await composer.fill('Please process this customer email: We need two replacement pumps next Friday.');
  await page.getByRole('button', { name: /Send/ }).click();

  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();
  const jsonButton = page.getByRole('button', { name: 'Download JSON' });
  await expect(jsonButton).toBeVisible();
  const downloadPromise = page.waitForEvent('download');
  await jsonButton.click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.json$/);
  expect(state.runBodies).toHaveLength(1);
  expect(state.runBodies[0]).toMatchObject({
    origin: 'chat_saved_workflow', history_visibility: 'global', workflow_id: 'verder_email_intake',
    inputs: { message: expect.stringContaining('replacement pumps') },
  });
  const submittedYaml = String(state.runBodies[0].workflow_yaml);
  expect(submittedYaml).toContain('id: prepare_inputs');
  expect(submittedYaml).toContain('workflow: verder_email_intake');
  expect(submittedYaml).toContain('id: answer');
  expect(submittedYaml).toContain('structured_result');
  expect(submittedYaml).not.toContain("inputs:\n  email_text:\n    type: text");
  await expect(page.getByText('REQUIRED_INPUT_MISSING', { exact: false })).toHaveCount(0);

  await page.reload();
  await expect(page.getByRole('button', { name: 'Download JSON' })).toBeVisible();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last()).toBeVisible();
});


test('existing workflow picker opens the saved workflow without preparing a replacement', async ({ page }) => {
  await installApi(page, { exposeExistingWorkflow: true });
  await page.goto('/chat');
  await expect(page.getByLabel('Studio panel')).toHaveCount(0);
  const prepareRequests: string[] = [];
  page.on('request', request => {
    if (new URL(request.url()).pathname === '/api/chat-workspace/prepare') prepareRequests.push(request.url());
  });
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  const picker = page.getByRole('dialog', { name: 'Use existing workflow' });
  await expect(picker.getByText('Customer triage')).toBeVisible();
  await expect(picker.getByText('Pump ICP2')).toBeVisible();
  await picker.getByPlaceholder('Search by name or purpose…').fill('pump icp2');
  await picker.getByText('Pump ICP2').click();
  await expect(page).toHaveURL(new RegExp(`/chat/private/${WORKFLOW_ID}\\?chat=`));
  expect(prepareRequests).toHaveLength(0);
  const localHistory = await page.evaluate(() => JSON.parse(window.localStorage.getItem('eurskem.chat.local-history.v1') ?? '{}'));
  expect(localHistory.chats[0]).toMatchObject({ title: 'Pump ICP2', workflowId: WORKFLOW_ID, workflowSource: 'private' });
});


test('Knowledge collection remains in Sources after opening an existing workflow and refresh', async ({ page }) => {
  await installApi(page, { exposeExistingWorkflow: true, exposeCollection: true });
  await page.goto('/chat');
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await page.getByRole('button', { name: '+ Add source' }).click();
  const sourcePicker = page.getByRole('dialog', { name: 'Add sources' });
  await sourcePicker.getByRole('button', { name: /Pump ICP2 Collection/ }).click();
  await expect(page.getByText('Pump ICP2 Collection', { exact: true })).toBeVisible();
  await expect(page.getByText('2 documents · ready', { exact: true })).toBeVisible();
  await expect(page.getByText('Pump Manual.pdf', { exact: true })).toBeVisible();

  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Chat', exact: true }).click();
  await page.getByRole('button', { name: 'Workflows', exact: true }).click();
  await page.getByRole('dialog', { name: 'Use existing workflow' }).getByText('Pump ICP2').click();
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByText('Pump ICP2 Collection', { exact: true })).toBeVisible();
  await page.reload();
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByText('Pump ICP2 Collection', { exact: true })).toBeVisible();
  await expect(page.getByText('Pump Specification.docx', { exact: true })).toBeVisible();
});


test('selected Knowledge collection automatically uses its active RAG Agent', async ({ page }) => {
  const state = await installApi(page, { exposeCollection: true, exposeRagAgent: true });
  await page.goto('/chat');
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await page.getByRole('button', { name: '+ Add source' }).click();
  await page.getByRole('dialog', { name: 'Add sources' }).getByRole('button', { name: /Pump ICP2 Collection/ }).click();
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Chat', exact: true }).click();

  const question = 'What maintenance interval does the pump manual specify?';
  await page.getByPlaceholder('Ask anything about your sources…').fill(question);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page).toHaveURL(new RegExp(`/chat/private/${RAG_WORKFLOW_ID}`));
  await expect(page.getByText('Choose an existing Knowledge workflow before sending this request.')).toHaveCount(0);
  await page.getByRole('button', { name: /Send/ }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: NO_KNOWLEDGE_ANSWER }).last()).toBeVisible();
  await expect(page.getByLabel('Active Knowledge scope')).toContainText('Pump ICP2 Collection · Pump Knowledge Assistant');
  for (const technicalText of ['Start', 'Rag', 'Reply', 'Data: [structured value]', 'Retrieval Trace Id', 'Result: [structured value]']) {
    await expect(page.getByText(technicalText, { exact: false })).toHaveCount(0);
  }
  await page.getByRole('button', { name: 'Broaden the question' }).click();
  await expect(page.getByPlaceholder('Ask anything about your sources…')).toHaveValue(`Answer this more broadly using the selected Knowledge: ${question}`);
  await page.getByPlaceholder('Ask anything about your sources…').fill('');
  await page.getByRole('button', { name: 'Choose other sources' }).click();
  if ((page.viewportSize()?.width ?? 1280) <= 1180) {
    await expect(page.getByRole('complementary', { name: 'Sources panel' })).toBeVisible();
    await page.getByRole('button', { name: 'Chat', exact: true }).click();
  }
  await page.getByRole('button', { name: 'Ask without Knowledge' }).click();
  await expect(page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: UNGROUNDED_ANSWER }).last()).toBeVisible();
  await expect(page.getByText('General answer · not grounded in selected Knowledge')).toBeVisible();

  expect(state.prepareBodies).toEqual([{
    objective: question, collection_id: 'pump-collection', rag_agent_id: 'rag-pump',
  }]);
  expect(state.runBodies).toHaveLength(2);
  expect(state.runBodies[0]).toMatchObject({
    history_visibility: 'conversation_only', workflow_id: RAG_WORKFLOW_ID,
    inputs: { message: question },
  });
  expect((state.runBodies[0].inputs as Record<string, unknown>).conversation_summary).toBeUndefined();
  expect(String(state.runBodies[0].workflow_yaml)).toContain('type: RAGAgent');
  expect(String(state.runBodies[0].workflow_yaml)).toContain('rag_agent_id: rag-pump');
  expect(state.runBodies[1]).toMatchObject({
    history_visibility: 'conversation_only', workflow_id: WORKFLOW_ID,
    inputs: { message: question },
  });
  expect(String(state.runBodies[1].workflow_yaml)).not.toContain('RAGAgent');

  const followUp = 'Which section contains that maintenance guidance?';
  await page.getByPlaceholder('Ask anything about your sources…').fill(followUp);
  await page.getByRole('button', { name: /Send/ }).click();
  await expect.poll(() => state.runBodies.length).toBe(3);
  expect(state.prepareBodies).toHaveLength(1);
  expect(state.runBodies[2]).toMatchObject({
    history_visibility: 'conversation_only', workflow_id: RAG_WORKFLOW_ID,
    inputs: { message: followUp },
  });
  expect(String((state.runBodies[2].inputs as Record<string, unknown>).conversation_summary)).toContain(`User: ${question}`);
  expect(String((state.runBodies[2].inputs as Record<string, unknown>).conversation_summary)).toContain(`Assistant: ${NO_KNOWLEDGE_ANSWER}`);
  expect(String((state.runBodies[2].inputs as Record<string, unknown>).conversation_summary)).not.toContain(UNGROUNDED_ANSWER);
  expect(state.runBodies[2].conversation_id).toBe(state.runBodies[0].conversation_id);

  await page.reload();
  if ((page.viewportSize()?.width ?? 1280) <= 1180) await page.getByRole('button', { name: 'Sources', exact: true }).click();
  await expect(page.getByText('Pump ICP2 Collection', { exact: true })).toBeVisible();
  const localHistory = await page.evaluate(() => JSON.parse(window.localStorage.getItem('eurskem.chat.local-history.v1') ?? '{}'));
  expect(localHistory.chats[0]).toMatchObject({ collectionId: 'pump-collection', ragAgentId: 'rag-pump' });
});


test('artifact-style follow-up stays in the selected saved workflow', async ({ page }) => {
  await installApi(page);
  await page.goto(`/chat/private/${WORKFLOW_ID}`);
  await page.getByPlaceholder('Ask anything about your sources…').fill(OBJECTIVE);
  await page.getByRole('button', { name: /Send/ }).click();
  const assistant = page.locator('p.whitespace-pre-wrap:visible').filter({ hasText: ANSWER }).last();
  await expect(assistant).toBeVisible();

  const followUp = page.getByPlaceholder('Ask anything about your sources…');
  await followUp.fill('Turn that into an executive presentation');
  const followUpRequest = page.waitForRequest(request => (
    new URL(request.url()).pathname.endsWith('/chat') && request.method() === 'POST'
  ));
  const before = page.url();
  await page.getByRole('button', { name: /Send/ }).click();
  expect((await followUpRequest).postDataJSON()).toMatchObject({ question: 'Turn that into an executive presentation' });
  await expect(page).toHaveURL(before);
  await expect(page.getByText(/within this workflow conversation/)).toBeVisible();
});