import { useEffect, useRef, useState } from 'react';
import { api } from '../../api/client';
import type { RunChatTurn } from '../../api/types';

export function PromptDraftAssistant({
  typeName,
  fieldName,
  onInsert,
  onClose,
  label = 'Draft Prompt',
}: {
  typeName: string;
  fieldName: string;
  onInsert: (text: string) => void;
  onClose: () => void;
  // What this drafting session is for, e.g. "Draft Prompt" for an LLM
  // instruction field or "Draft Email" for an email body — shown in the
  // modal header so the affordance reads as specific to the field, not a
  // generic catch-all.
  label?: string;
}) {
  const [turns, setTurns] = useState<RunChatTurn[]>([]);
  const [instruction, setInstruction] = useState('');
  // 'chat' iterates one short message at a time; 'own' takes a whole block of
  // already-written instructions — in any language — and recreates it as a
  // ready-to-use prompt in one shot. Same endpoint, same model, just a
  // different input shape for a different authoring habit.
  const [mode, setMode] = useState<'chat' | 'own'>('chat');
  const [ownText, setOwnText] = useState('');
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  async function ask(text: string) {
    const trimmed = text.trim();
    if (!trimmed || asking) return;
    setError(null);
    setAsking(true);
    setInstruction('');
    setOwnText('');
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
            {label} — {typeName}.{fieldName}
          </h2>
          <button onClick={onClose} className="text-lg leading-none text-ink-500 hover:text-ink-900">×</button>
        </div>

        <div className="flex-none flex gap-1 border-b border-slate-200 px-3 pt-2">
          {(['chat', 'own'] as const).map(m => (
            <button
              key={m}
              type="button"
              onClick={() => setMode(m)}
              className={`rounded-t-md px-3 py-1.5 text-xs font-medium ${
                mode === m
                  ? 'bg-white border border-b-0 border-slate-200 text-ink-900'
                  : 'text-ink-500 hover:text-ink-700'
              }`}
            >
              {m === 'chat' ? 'Chat with AI' : 'Paste my instructions'}
            </button>
          ))}
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-[140px]">
          {turns.length === 0 && mode === 'chat' && (
            <div className="text-sm text-ink-500">
              Describe what this {fieldName} should do — e.g. "summarize competitor pricing pages
              and flag price drops."
            </div>
          )}
          {turns.length === 0 && mode === 'own' && (
            <div className="text-sm text-ink-500">
              Write your own instructions below — in whatever language is easiest for you —
              and it'll be recreated as a ready-to-use prompt for this field.
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

        {mode === 'chat' ? (
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
        ) : (
          <form
            onSubmit={e => { e.preventDefault(); ask(ownText); }}
            className="flex-none space-y-2 border-t border-slate-200 p-3"
          >
            <textarea
              value={ownText}
              onChange={e => setOwnText(e.target.value)}
              placeholder="Write your instructions here, in any language — e.g. „Fasse eingehende Preisanfragen zusammen und markiere Rabatte über 10 %.“"
              rows={4}
              disabled={asking}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={asking || !ownText.trim()}
              className="w-full rounded-md bg-accent-600 px-4 py-2 text-sm text-white hover:bg-accent-500 disabled:opacity-50"
            >
              {asking ? 'Recreating…' : 'Recreate prompt'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
