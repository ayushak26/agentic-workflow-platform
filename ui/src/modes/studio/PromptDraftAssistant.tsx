import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { RunChatTurn } from '../../api/types';

export function PromptDraftAssistant({
  typeName,
  fieldName,
  onInsert,
  onClose,
}: {
  typeName: string;
  fieldName: string;
  onInsert: (text: string) => void;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<RunChatTurn[]>([]);
  const [instruction, setInstruction] = useState('');
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || asking) return;
    setError(null);
    setAsking(true);
    setInstruction('');
    const withQuestion: RunChatTurn[] = [
      ...turns,
      { role: 'user', content: trimmed, ts: Date.now() / 1000 },
    ];
    setTurns(withQuestion);
    try {
      const result = await api.draftPrompt(typeName, fieldName, trimmed, turns);
      setTurns([...withQuestion, { role: 'assistant', content: result.answer, ts: Date.now() / 1000 }]);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setTurns(turns);
    } finally {
      setAsking(false);
    }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns]);

  const lastAssistantDraft = [...turns].reverse().find(t => t.role === 'assistant')?.content ?? null;

  return (
    <div className="fixed inset-0 bg-black/20 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-white rounded-lg shadow-xl w-full max-w-lg max-h-[70vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="px-4 py-3 border-b border-slate-200 flex items-center justify-between flex-none">
          <h2 className="text-sm font-semibold text-ink-900">
            Draft with AI — {typeName}.{fieldName}
          </h2>
          <button onClick={onClose} className="text-lg leading-none text-ink-500 hover:text-ink-900">×</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[140px]">
          {turns.length === 0 && (
            <div className="text-sm text-ink-500">
              Describe what this {fieldName} should do — e.g. "summarize competitor pricing pages
              and flag price drops."
            </div>
          )}
          {turns.map((turn, i) => (
            <div key={i}>
              <div
                className={`max-w-[90%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${
                  turn.role === 'user' ? 'ml-auto bg-accent-600 text-white' : 'bg-slate-100 text-ink-900 font-mono text-xs'
                }`}
              >
                {turn.content}
              </div>
              {turn.role === 'assistant' && (
                <button
                  onClick={() => { onInsert(turn.content); onClose(); }}
                  className="mt-1 text-xs text-accent-700 hover:underline"
                >
                  Use this draft →
                </button>
              )}
            </div>
          ))}
          {asking && (
            <div className="bg-slate-100 text-ink-500 rounded-lg px-3 py-2 text-sm w-fit">Drafting…</div>
          )}
          <div ref={bottomRef} />
        </div>

        {error && (
          <div className="mx-4 mb-2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-700">
            {error}
          </div>
        )}

        <form
          onSubmit={e => { e.preventDefault(); ask(instruction); }}
          className="flex-none flex items-center gap-2 border-t border-slate-200 p-3"
        >
          <input
            value={instruction}
            onChange={e => setInstruction(e.target.value)}
            placeholder={lastAssistantDraft ? 'Ask for changes…' : 'What should this prompt do?'}
            disabled={asking}
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={asking || !instruction.trim()}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            {lastAssistantDraft ? 'Revise' : 'Draft'}
          </button>
        </form>
      </div>
    </div>
  );
}
