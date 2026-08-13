import type { StepExplanation } from '../../../api/types';
import { EXECUTION_KINDS } from './ExecutionKindBadge';

/**
 * "Why did this happen?" (§24, §47).
 *
 * The single most important thing to be able to show an interviewer, a
 * colleague, or an auditor: not just what a step produced, but what *kind of
 * thing* decided it and which specific conditions were true. A model's answer
 * is reported as a model's answer; a rule's answer is reported with the rule.
 *
 * Rendered identically in the node Test tab and in the Simulator, because the
 * explanation is a property of the step, not of the surface showing it.
 */

export function ExplanationView({
  explanation,
  compact = false,
}: {
  explanation: StepExplanation | undefined;
  compact?: boolean;
}) {
  if (!explanation) return null;
  const meta = EXECUTION_KINDS[explanation.kind] ?? EXECUTION_KINDS.deterministic;

  return (
    <div className={`rounded-md border ${meta.className} p-2`}>
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold">
          {explanation.decided_by ?? meta.label}
        </span>
        {typeof explanation.confidence === 'number' && (
          <span className="text-[10px]">
            confidence {(explanation.confidence * 100).toFixed(0)}%
          </span>
        )}
      </div>

      {explanation.route && (
        <div className="mt-1 text-[11px]">
          Branch: <span className="font-mono font-semibold">{explanation.route}</span>
          {explanation.used_fallback && (
            <span className="ml-1 rounded bg-amber-100 px-1 text-[9px] text-amber-800">
              fallback
            </span>
          )}
        </div>
      )}

      {explanation.summary.length > 0 && (
        <ul className="mt-1 space-y-0.5">
          {explanation.summary.slice(0, compact ? 4 : 20).map((line, index) => (
            <li className="flex gap-1 text-[10px] leading-4" key={index}>
              <span aria-hidden className="flex-none">✓</span>
              <span className="min-w-0 break-words font-mono">{line}</span>
            </li>
          ))}
        </ul>
      )}

      {explanation.matched_rules && explanation.matched_rules.length > 0 && (
        <div className="mt-1 text-[10px]">
          Matched rules: {explanation.matched_rules.join(', ')}
        </div>
      )}

      {explanation.status && explanation.status !== 'ok' && (
        <div className="mt-1 rounded bg-white/60 px-1.5 py-1 text-[10px]">
          Status: <span className="font-mono">{explanation.status}</span>
        </div>
      )}

      {!compact && explanation.reasoning && (
        <div className="mt-1 text-[10px] italic leading-4 opacity-80">
          {explanation.reasoning}
        </div>
      )}

      {!compact && (explanation.model_used || explanation.detected_language) && (
        <div className="mt-1 text-[10px] opacity-70">
          {explanation.model_used && <>model {explanation.model_used}</>}
          {explanation.model_used && explanation.detected_language && ' · '}
          {explanation.detected_language && <>language {explanation.detected_language}</>}
        </div>
      )}

      {explanation.deduplicated && (
        <div className="mt-1 text-[10px]">
          Already performed in this run — not repeated.
        </div>
      )}
    </div>
  );
}

/** Compact value renderer used by both the Test tab and the Simulator. */
export function ValueTree({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined) {
    return <span className="text-ink-400">null</span>;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-ink-400">empty list</span>;
    return (
      <ul className="space-y-0.5">
        {value.map((item, index) => (
          <li key={index} className="flex gap-1">
            <span className="flex-none text-ink-400">·</span>
            <ValueTree depth={depth + 1} value={item} />
          </li>
        ))}
      </ul>
    );
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>);
    if (entries.length === 0) return <span className="text-ink-400">empty</span>;
    return (
      <div className={depth > 0 ? 'border-l border-slate-200 pl-2' : ''}>
        {entries.map(([key, item]) => (
          <div className="flex gap-2 py-0.5" key={key}>
            <span className="flex-none font-mono text-[10px] text-ink-500">{key}</span>
            <span className="min-w-0 break-words text-[11px] text-ink-800">
              <ValueTree depth={depth + 1} value={item} />
            </span>
          </div>
        ))}
      </div>
    );
  }
  if (typeof value === 'boolean') {
    return (
      <span className={value ? 'font-semibold text-emerald-700' : 'text-ink-600'}>
        {String(value)}
      </span>
    );
  }
  return <span>{String(value)}</span>;
}
