import { useEffect, useRef, useState } from 'react';
import { api } from '../../../api/client';
import type { RunChatTurn } from '../../../api/types';

function formatTime(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function Avatar({ role }: { role: 'user' | 'assistant' }) {
  return (
    <div
      className={`h-6 w-6 flex-none rounded-full flex items-center justify-center text-[11px] font-semibold ${
        role === 'user' ? 'bg-accent-600 text-white' : 'bg-slate-700 text-white'
      }`}
    >
      {role === 'user' ? 'You' : 'AI'}
    </div>
  );
}

export function AskAiPanel({
  runId,
  // Lets a caller open the panel already holding the question the person
  // clicked — a suggested prompt in Business View should not make them retype
  // it. Left unsent: they can still edit or discard it.
  initialQuestion,
}: {
  runId: string;
  initialQuestion?: string;
}) {
  const [turns, setTurns] = useState<RunChatTurn[] | null>(null);
  const [starterQuestions, setStarterQuestions] = useState<string[]>([]);
  const [question, setQuestion] = useState(initialQuestion ?? '');
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    api.runChatHistory(runId)
      .then(data => {
        if (cancelled) return;
        setTurns(data.turns);
        setStarterQuestions(data.starter_questions);
      })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [runId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, asking]);

  useEffect(() => {
    inputRef.current?.focus();
  }, [runId]);

  async function ask(q: string) {
    const trimmed = q.trim();
    if (!trimmed || asking) return;
    setError(null);
    setAsking(true);
    setQuestion('');
    // Show the question immediately rather than waiting for the round trip —
    // the assistant's turn replaces this optimistic pair once it lands.
    setTurns(current => [...(current ?? []), { role: 'user', content: trimmed, ts: Date.now() / 1000 }]);
    try {
      const result = await api.askAboutRun(runId, trimmed, turns ?? []);
      setTurns(result.turns);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      // Roll back the optimistic question so it doesn't look answered.
      setTurns(current => (current ?? []).slice(0, -1));
    } finally {
      setAsking(false);
      inputRef.current?.focus();
    }
  }

  if (turns === null && !error) {
    return <div className="p-4 text-sm text-ink-500">Loading…</div>;
  }

  return (
    <div className="h-full flex flex-col min-h-0 bg-slate-50">
      <div className="flex-none flex items-center gap-2 border-b border-slate-200 bg-white px-4 py-2.5">
        <div className="h-7 w-7 rounded-full bg-accent-600 text-white flex items-center justify-center text-xs font-semibold">
          AI
        </div>
        <div>
          <div className="text-sm font-semibold text-ink-900">Ask AI about this run</div>
          <div className="text-[11px] text-ink-500">Grounded in this run's own data — status, inputs, nodes, prompts</div>
        </div>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-4 space-y-4">
        {turns !== null && turns.length === 0 && (
          <div className="space-y-3">
            <div className="text-sm text-ink-500">
              Ask a question about this run — what happened, what failed, what
              inputs and evidence were used. A few to start:
            </div>
            <div className="flex flex-col gap-2 items-start">
              {starterQuestions.map(q => (
                <button
                  key={q}
                  onClick={() => ask(q)}
                  disabled={asking}
                  className="px-3 py-1.5 rounded-full border border-accent-300 bg-white text-accent-700 text-xs hover:bg-accent-50 disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        {(turns ?? []).map((turn, i) => (
          <div key={i} className={`flex items-end gap-2 ${turn.role === 'user' ? 'flex-row-reverse' : ''}`}>
            <Avatar role={turn.role} />
            <div className={`max-w-[78%] flex flex-col ${turn.role === 'user' ? 'items-end' : 'items-start'}`}>
              <div
                className={`rounded-2xl px-3.5 py-2 text-sm whitespace-pre-wrap shadow-sm ${
                  turn.role === 'user'
                    ? 'bg-accent-600 text-white rounded-br-sm'
                    : 'bg-white text-ink-900 border border-slate-200 rounded-bl-sm'
                }`}
              >
                {turn.content}
              </div>
              <div className="text-[10px] text-ink-400 mt-1 px-1">{formatTime(turn.ts)}</div>
            </div>
          </div>
        ))}
        {asking && (
          <div className="flex items-end gap-2">
            <Avatar role="assistant" />
            <div className="bg-white border border-slate-200 text-ink-500 rounded-2xl rounded-bl-sm px-3.5 py-2 text-sm w-fit shadow-sm">
              <span className="inline-flex gap-1">
                <span className="animate-bounce [animation-delay:-0.3s]">.</span>
                <span className="animate-bounce [animation-delay:-0.15s]">.</span>
                <span className="animate-bounce">.</span>
              </span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {error && (
        <div className="mx-4 mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700">
          {error}
        </div>
      )}

      <form
        onSubmit={e => { e.preventDefault(); ask(question); }}
        className="flex-none flex items-center gap-2 border-t border-slate-200 bg-white p-3"
      >
        <input
          ref={inputRef}
          value={question}
          onChange={e => setQuestion(e.target.value)}
          placeholder="Ask anything about this run…"
          disabled={asking}
          className="flex-1 rounded-full border border-slate-300 px-4 py-2 text-sm disabled:opacity-50 focus:outline-none focus:ring-2 focus:ring-accent-300"
        />
        <button
          type="submit"
          disabled={asking || !question.trim()}
          className="px-4 py-2 rounded-full bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
        >
          Send
        </button>
      </form>
    </div>
  );
}
