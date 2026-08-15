import { useState } from 'react';
import { AskAiDialog } from './AskAiDialog';
import { FEATURE_HELP } from './feature-help';

/**
 * The reusable "ⓘ Info" trigger for a Builder feature (Problem 1). Renders
 * purely static copy from feature-help.ts — no LLM call — plus an "Ask AI"
 * button that hands the same copy to AskAiDialog as compact context, so the
 * dynamic follow-up never needs a second explanation authored server-side.
 *
 * Usage: <InfoPopover feature="preflight" />
 */
export function InfoPopover({ feature, align = 'left' }: { feature: string; align?: 'left' | 'right' }) {
  const [open, setOpen] = useState(false);
  const [askingAi, setAskingAi] = useState(false);
  const entry = FEATURE_HELP[feature];

  if (!entry) return null;

  return (
    <span className="relative inline-flex">
      <button
        aria-expanded={open}
        aria-label={`About ${entry.title}`}
        className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[11px] leading-none text-ink-400 hover:text-accent-700"
        onClick={event => { event.stopPropagation(); setOpen(value => !value); }}
        title={`About ${entry.title}`}
        type="button"
      >
        ⓘ
      </button>

      {open && (
        <div
          className={`absolute top-6 z-40 w-72 rounded-lg border border-slate-200 bg-white p-3 text-xs shadow-panel ${align === 'right' ? 'right-0' : 'left-0'}`}
          onClick={event => event.stopPropagation()}
        >
          <div className="mb-2 flex items-center justify-between gap-2">
            <div className="text-[11px] font-semibold text-ink-900">{entry.title}</div>
            <button aria-label="Close" className="text-ink-400 hover:text-ink-900" onClick={() => setOpen(false)} type="button">×</button>
          </div>
          <dl className="space-y-1.5">
            <Row label="What is this?" value={entry.description} />
            <Row label="When should I use it?" value={entry.whenToUse} />
            <Row label="What changes in the workflow?" value={entry.effect} />
            <Row label="Example" value={entry.example} />
          </dl>
          <button
            className="mt-2 w-full rounded border border-slate-200 py-1 text-[11px] font-medium text-accent-700 hover:bg-accent-50"
            onClick={() => { setAskingAi(true); setOpen(false); }}
            type="button"
          >
            Ask AI
          </button>
        </div>
      )}

      {askingAi && (
        <AskAiDialog
          context={{ feature: entry.id, feature_description: entry.description }}
          onClose={() => setAskingAi(false)}
          starterQuestion={`Explain the "${entry.title}" feature in the Workflow Builder — what it does and when I should use it.`}
          title={`Ask AI — ${entry.title}`}
        />
      )}
    </span>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="font-semibold text-ink-700">{label}</dt>
      <dd className="mt-0.5 leading-4 text-ink-600">{value}</dd>
    </div>
  );
}
