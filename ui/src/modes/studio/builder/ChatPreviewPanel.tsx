import { useMemo, useRef, useState, type ReactNode } from 'react';

import { api } from '../../../api/client';
import type { SimulationStep } from '../../../api/types';
import { RagAnswerView, type RagRelevantContextItemView, type RagSourceView } from '../../../components/rag/RagAnswerView';
import { JsonTree } from '../cockpit/JsonTree';
import type { YamlWorkflow } from '../yaml-bridge';

/**
 * Try the workflow as a chatbot, live, from inside the Builder (§1-§46 of the
 * Chat Preview spec).
 *
 * Built entirely on the existing `/api/builder/simulate` endpoint — the exact
 * mechanism SimulatorPanel already uses, which runs the real draft workflow
 * synchronously with no run record. Nothing here re-implements execution;
 * this panel only shapes a chat message into the workflow's inputs and
 * renders whichever End node's output comes back.
 *
 * Conversation history lives only in this panel's own state — no new
 * server-side conversation storage. Each send is an independent, stateless
 * `/simulate` call; the running transcript is passed along as an optional
 * `conversation_history` input in case a workflow author wants to reference
 * it, but nothing here requires that.
 */

type Turn =
  | { role: 'user'; content: string }
  | {
    role: 'assistant';
    content: string;
    sources: RagSourceView[];
    routeTo?: string;
    routeToLabel?: string;
    steps: SimulationStep[];
    fullOutput: unknown;
  }
  | { role: 'error'; content: string; detail?: string };

type ChatOutput = {
  outcome?: string;
  message?: string;
  route_to?: string;
  route_to_label?: string;
  sources?: RagSourceView[];
  handoff?: Record<string, unknown>;
};

const SEND_TIMEOUT_MS = 60_000;

function findChatbotStart(workflow: YamlWorkflow): Record<string, unknown> | undefined {
  const node = workflow.nodes.find(
    item => item.type === 'StartAgent' && (item.config?.mode ?? 'input_form') === 'chatbot',
  );
  return node?.config;
}

function asChatOutput(value: unknown): ChatOutput | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  return value as ChatOutput;
}

function humanize(value: string): string {
  return value.replace(/[_-]/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
}

export function ChatPreviewPanel({
  workflow,
  workflowYaml,
}: {
  workflow: YamlWorkflow;
  workflowYaml: string;
}) {
  const startConfig = useMemo(() => findChatbotStart(workflow), [workflow]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [openInspector, setOpenInspector] = useState<number | null>(null);
  const controllerRef = useRef<AbortController | null>(null);

  const chatbotName = typeof startConfig?.chatbot_name === 'string' && startConfig.chatbot_name
    ? startConfig.chatbot_name : 'Chat Preview';
  const welcome = typeof startConfig?.welcome_message === 'string' ? startConfig.welcome_message : '';
  const placeholder = typeof startConfig?.message_placeholder === 'string' && startConfig.message_placeholder
    ? startConfig.message_placeholder : 'Ask a question...';
  const suggested = Array.isArray(startConfig?.suggested_questions)
    ? (startConfig.suggested_questions as unknown[]).filter((q): q is string => typeof q === 'string' && q.length > 0)
    : [];

  const reset = () => {
    controllerRef.current?.abort();
    setTurns([]);
    setInput('');
    setBusy(false);
    setOpenInspector(null);
  };

  const send = async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setInput('');
    const history = turns
      .filter((turn): turn is Extract<Turn, { role: 'user' | 'assistant' }> => turn.role === 'user' || turn.role === 'assistant')
      .map(turn => ({ role: turn.role, content: turn.content }));
    setTurns(current => [...current, { role: 'user', content: trimmed }]);
    setBusy(true);

    const controller = new AbortController();
    controllerRef.current = controller;
    const timer = window.setTimeout(() => controller.abort(), SEND_TIMEOUT_MS);

    try {
      const result = await api.simulateWorkflow({
        workflow_yaml: workflowYaml,
        inputs: { message: trimmed, attachments: [], conversation_history: history },
      }, controller.signal);

      if (result.error) {
        setTurns(current => [...current, { role: 'error', content: 'The workflow could not complete this request.', detail: result.error }]);
        return;
      }
      const output = asChatOutput(result.output);
      if (!output || !output.message) {
        setTurns(current => [...current, {
          role: 'error',
          content: 'The workflow finished without a Chat Response — add or check the End node.',
          detail: JSON.stringify(result.output ?? result.steps.at(-1)?.output ?? {}, null, 2),
        }]);
        return;
      }
      setTurns(current => [...current, {
        role: 'assistant',
        content: output.message ?? '',
        sources: Array.isArray(output.sources) ? output.sources : [],
        routeTo: output.outcome === 'route' ? output.route_to : undefined,
        routeToLabel: output.outcome === 'route' ? (output.route_to_label ?? (output.route_to ? humanize(output.route_to) : undefined)) : undefined,
        steps: result.steps,
        fullOutput: result.output,
      }]);
    } catch (reason) {
      const aborted = reason instanceof DOMException && reason.name === 'AbortError';
      setTurns(current => [...current, {
        role: 'error',
        content: aborted ? 'The workflow took too long to respond.' : 'The workflow could not complete this request.',
        detail: aborted ? undefined : (reason instanceof Error ? reason.message : String(reason)),
      }]);
    } finally {
      window.clearTimeout(timer);
      controllerRef.current = null;
      setBusy(false);
    }
  };

  if (!startConfig) {
    return (
      <div className="p-4 text-[11px] text-ink-500">
        Chat Preview is available once this workflow&apos;s Start node is set to
        Chatbot Interface mode.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <div className="truncate text-[11px] font-semibold text-ink-900">{chatbotName}</div>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={reset}
          type="button"
        >
          New Conversation
        </button>
      </div>

      <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
        {turns.length === 0 && welcome && (
          <ChatBubble role="assistant"><p>{welcome}</p></ChatBubble>
        )}
        {turns.length === 0 && suggested.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {suggested.map((question, index) => (
              <button
                className="rounded-full border border-slate-200 bg-white px-2 py-1 text-[11px] text-ink-700 hover:border-accent-400"
                key={index}
                onClick={() => void send(question)}
                type="button"
              >
                {question}
              </button>
            ))}
          </div>
        )}

        {turns.map((turn, index) => (
          <div key={index}>
            {turn.role === 'user' && (
              <ChatBubble role="user"><p className="whitespace-pre-wrap">{turn.content}</p></ChatBubble>
            )}
            {turn.role === 'error' && (
              <ChatBubble role="error">
                <p>{turn.content}</p>
                {turn.detail && (
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[10px] text-ink-400">View error</summary>
                    <pre className="mt-1 max-h-40 overflow-auto rounded bg-white p-2 text-[10px] text-ink-600">{turn.detail}</pre>
                  </details>
                )}
              </ChatBubble>
            )}
            {turn.role === 'assistant' && (
              <ChatBubble role="assistant">
                <RagAnswerView
                  answer={turn.content}
                  relevantContext={[] as RagRelevantContextItemView[]}
                  sources={turn.sources}
                />
                {turn.routeTo && (
                  <div className="mt-1.5 text-[11px] font-medium text-emerald-700">
                    ✓ Routed to {turn.routeToLabel}
                  </div>
                )}
                <button
                  className="mt-1.5 text-[10px] font-medium text-ink-400 hover:text-ink-700 hover:underline"
                  onClick={() => setOpenInspector(openInspector === index ? null : index)}
                  type="button"
                >
                  {openInspector === index ? 'Hide execution' : 'View execution'}
                </button>
                {openInspector === index && (
                  <div className="mt-1.5 space-y-2 rounded border border-slate-200 bg-slate-50 p-2">
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">Result</div>
                    <JsonTree defaultCollapsedDepth={1} searchable={false} value={(turn.fullOutput as never) ?? {}} />
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">Steps</div>
                    <ul className="space-y-0.5 text-[11px] text-ink-700">
                      {turn.steps.map((step, stepIndex) => (
                        <li key={stepIndex}>{step.label} <span className="font-mono text-[10px] text-ink-400">{step.node_id}</span></li>
                      ))}
                    </ul>
                  </div>
                )}
              </ChatBubble>
            )}
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2 border-t border-slate-200 p-2">
        <input
          className="builder-field flex-1"
          disabled={busy}
          onChange={event => setInput(event.target.value)}
          onKeyDown={event => { if (event.key === 'Enter') void send(input); }}
          placeholder={placeholder}
          value={input}
        />
        <button
          className="ui-button ui-button--primary flex-none"
          disabled={busy || !input.trim()}
          onClick={() => void send(input)}
          type="button"
        >
          {busy ? 'Running…' : 'Send'}
        </button>
      </div>
    </div>
  );
}

function ChatBubble({ role, children }: { role: 'user' | 'assistant' | 'error'; children: ReactNode }) {
  if (role === 'user') {
    return (
      <div className="ml-8 rounded-lg bg-accent-600 px-3 py-2 text-[12px] text-white">
        {children}
      </div>
    );
  }
  if (role === 'error') {
    return (
      <div className="mr-8 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-800">
        {children}
      </div>
    );
  }
  return (
    <div className="mr-8 rounded-lg border border-slate-200 bg-white px-3 py-2 text-[12px] text-ink-900">
      {children}
    </div>
  );
}
