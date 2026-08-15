import { useEffect, useRef, useState } from 'react';
import { api } from '../../../api/client';
import type { AskContext, RunChatTurn } from '../../../api/types';

/**
 * The shared Ask AI chat surface (Problem 3) — a small modal thread over
 * POST /api/node-types/ask. Generalizes what was NodeTypeAskAi.tsx (still a
 * thin wrapper over this, see that file) so a Builder *feature* question and
 * a node-*type* question share one implementation instead of two.
 *
 * `context` is the compact, structured "what did the user click" payload
 * (see AskContext) — never the whole workflow. The backend uses it to look
 * up only the relevant node-type manifest entries instead of the full ~50
 * registered types.
 */
export function AskAiDialog({
  title,
  starterQuestion,
  context,
  onClose,
}: {
  title: string;
  starterQuestion: string;
  context?: AskContext;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<RunChatTurn[]>([]);
  const [question, setQuestion] = useState('');
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const askedInitially = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function ask(q: string) {
    const trimmed = q.trim();
    if (!trimmed || asking) return;
    setError(null);
    setAsking(true);
    setQuestion('');
    const withQuestion: RunChatTurn[] = [
      ...turns,
      { role: 'user', content: trimmed, ts: Date.now() / 1000 },
    ];
    setTurns(withQuestion);
    try {
      const result = await api.askAboutNodeTypes(trimmed, context?.node_type, turns, context);
      setTurns([...withQuestion, { role: 'assistant', content: result.answer, ts: Date.now() / 1000 }]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setTurns(turns);
    } finally {
      setAsking(false);
    }
  }

  useEffect(() => {
    if (askedInitially.current) return;
    askedInitially.current = true;
    ask(starterQuestion);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  return (
    <div className="fixed inset-0 bg-black/20 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-lg max-h-[70vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between flex-none">
          <h2 className="text-sm font-semibold text-ink-900">{title}</h2>
          <button onClick={onClose} className="text-lg leading-none text-ink-500 hover:text-ink-900">×</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[160px]">
          {turns.map((turn, i) => (
            <div
              key={i}
              className={`max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                turn.role === 'user' ? 'ml-auto bg-accent-600 text-white' : 'bg-slate-100 text-ink-900'
              }`}
            >
              {turn.content}
            </div>
          ))}
          {asking && (
            <div className="bg-slate-100 text-ink-500 rounded-lg px-3 py-2 text-sm w-fit">Thinking…</div>
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
          className="flex-none flex items-center gap-2 border-t border-slate-200 p-3"
        >
          <input
            value={question}
            onChange={e => setQuestion(e.target.value)}
            placeholder="Ask a follow-up…"
            disabled={asking}
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={asking || !question.trim()}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            Ask
          </button>
        </form>
      </div>
    </div>
  );
}
