import type { ReactNode } from 'react';

import type {
  AIModelUsage,
  BusinessAction,
  BusinessFact,
  BusinessSource,
} from '../../../api/types';
import { formatCost } from './format';

/**
 * Shared pieces of the Business View's visual language.
 *
 * Two rules run through all of them, both from the accessibility brief (§68):
 * meaning is never carried by colour alone — every state also has a word or a
 * symbol — and every control is a real focusable `<button>` with a label that
 * makes sense read aloud.
 */

export function Card({
  title, subtitle, tone = 'plain', action, children, className = '',
}: {
  title?: string;
  subtitle?: string;
  tone?: 'plain' | 'attention' | 'primary' | 'quiet';
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const toneClass = {
    plain: 'border-slate-200 bg-white',
    attention: 'border-warn/40 bg-warn/5',
    primary: 'border-accent-200 bg-accent-50',
    // Completed work should recede rather than compete with what still needs
    // a person (§41).
    quiet: 'border-slate-200 bg-slate-50/60',
  }[tone];

  return (
    <section className={`rounded-lg border p-4 ${toneClass} ${className}`}>
      {(title || action) && (
        <div className="flex items-start justify-between gap-3">
          <div>
            {title && (
              <h2 className="text-xs font-semibold uppercase tracking-wide text-ink-400">{title}</h2>
            )}
            {subtitle && <p className="mt-0.5 text-sm text-ink-500">{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      <div className={title || action ? 'mt-3' : ''}>{children}</div>
    </section>
  );
}

const SOURCE_STYLE: Record<BusinessSource, string> = {
  ai: 'bg-accent-100 text-accent-700',
  rule: 'bg-slate-100 text-ink-700',
  system: 'bg-ok/15 text-ok',
  human: 'bg-warn/15 text-warn',
  customer_message: 'bg-slate-100 text-ink-500',
  workflow: 'bg-slate-100 text-ink-500',
};

/**
 * Where a result came from — an AI badge names the model that actually ran,
 * and a rule-based or ERP-sourced result never gets one (§21, §25).
 */
export function SourceBadge({ source, label }: { source: BusinessSource; label: string }) {
  return (
    <span
      className={`inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium ${SOURCE_STYLE[source]}`}
    >
      {label}
    </span>
  );
}

/** Model, latency and cost — each shown only where a real figure exists (§24). */
export function ModelMeta({ ai }: { ai: AIModelUsage }) {
  const parts: string[] = [];
  if (ai.latency_ms != null) parts.push(`${(ai.latency_ms / 1000).toFixed(1)}s`);
  if (ai.cost_usd != null) parts.push(formatCost(ai.cost_usd));
  if (!parts.length) return null;
  return <span className="text-[11px] text-ink-400">{parts.join(' · ')}</span>;
}

const EMPHASIS_STYLE = {
  primary: 'bg-accent-600 text-white hover:bg-accent-700 border-transparent',
  secondary: 'border-slate-300 text-ink-700 hover:bg-slate-50 bg-white',
  danger: 'border-bad/40 text-bad hover:bg-bad/5 bg-white',
} as const;

export function ActionButton({
  action, onRun, busy = false, size = 'md',
}: {
  action: BusinessAction;
  onRun: (action: BusinessAction) => void;
  busy?: boolean;
  size?: 'sm' | 'md';
}) {
  const disabled = !action.enabled || busy;
  return (
    <button
      type="button"
      onClick={() => onRun(action)}
      disabled={disabled}
      title={action.disabled_reason ?? action.description ?? undefined}
      aria-label={
        action.requires_approval ? `${action.label} (prepares a draft for your approval)` : action.label
      }
      className={`inline-flex items-center gap-1.5 rounded-md border font-medium disabled:opacity-50 ${
        EMPHASIS_STYLE[action.emphasis]
      } ${size === 'sm' ? 'px-2 py-1 text-xs' : 'px-3 py-1.5 text-sm'}`}
    >
      {action.label}
      {action.requires_approval && (
        // Says out loud that clicking prepares something rather than doing it
        // (§54) — never encoded as a colour alone.
        <span className="text-[10px] font-normal opacity-70">· draft</span>
      )}
    </button>
  );
}

export function ActionRow({
  actions, onRun, busyId, size = 'md',
}: {
  actions: BusinessAction[];
  onRun: (action: BusinessAction) => void;
  busyId?: string | null;
  size?: 'sm' | 'md';
}) {
  if (!actions.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      {actions.map(action => (
        <ActionButton
          key={action.id}
          action={action}
          onRun={onRun}
          busy={busyId === action.id}
          size={size}
        />
      ))}
    </div>
  );
}

/**
 * A two-column field grid (§39). A value the workflow could not establish is
 * shown muted and in words — "Not stated" — rather than omitted, because an
 * absent commercial fact is itself information.
 */
export function FactGrid({
  facts, onRun, showSource = true, columns = 2,
}: {
  facts: BusinessFact[];
  onRun?: (action: BusinessAction, fact: BusinessFact) => void;
  showSource?: boolean;
  columns?: 1 | 2;
}) {
  if (!facts.length) return null;
  return (
    <dl className={`grid gap-x-6 gap-y-3 ${columns === 2 ? 'sm:grid-cols-2' : ''}`}>
      {facts.map(fact => (
        <div key={fact.id} className="min-w-0">
          <dt className="flex flex-wrap items-center gap-1.5 text-xs text-ink-400">
            {fact.label}
            {fact.stale && (
              <span
                title="Worked out before a related fact was corrected — recheck before relying on it."
                className="rounded-full bg-warn/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warn"
              >
                Recheck
              </span>
            )}
          </dt>
          <dd className="mt-0.5 flex flex-wrap items-baseline gap-2 break-words">
            <span className={fact.missing ? 'italic text-ink-400' : 'text-ink-900'}>
              {fact.display}
            </span>
            {showSource && <SourceBadge source={fact.source} label={fact.source_label} />}
            {onRun && fact.editable && fact.actions.map(action => (
              <button
                key={action.id}
                type="button"
                onClick={() => onRun(action, fact)}
                className="text-xs font-medium text-accent-600 hover:underline"
                aria-label={`Edit ${fact.label}`}
              >
                {action.label}
              </button>
            ))}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** A short "✓ …" evidence list. The tick is decorative; the text carries it. */
export function CheckList({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <ul className="flex flex-col gap-1 text-sm text-ink-700">
      {items.map(item => (
        <li key={item} className="flex items-start gap-2">
          <span aria-hidden="true" className="mt-0.5 text-ok">✓</span>
          <span>{item}</span>
        </li>
      ))}
    </ul>
  );
}

export function Modal({
  title, description, onClose, children, wide = false,
}: {
  title: string;
  description?: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onKeyDown={event => { if (event.key === 'Escape') onClose(); }}
    >
      <div className={`flex max-h-[90vh] w-full flex-col overflow-hidden rounded-lg bg-white ${wide ? 'max-w-4xl' : 'max-w-lg'}`}>
        <div className="flex flex-none items-start justify-between gap-4 border-b border-slate-200 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-ink-900">{title}</div>
            {description && <div className="text-xs text-ink-400">{description}</div>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex-none text-sm text-ink-500 hover:text-ink-900"
          >
            Close
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </div>
    </div>
  );
}
