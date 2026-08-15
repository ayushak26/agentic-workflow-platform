import { useEffect, useState, type FormEvent } from 'react';

import { api } from '../../../api/client';
import type { BusinessTechnicalDetail } from '../../../api/types';
import { formatCost } from './format';
import { Modal } from './primitives';
import type { ActionOutcome, ActionPrompt } from './useBusinessActions';

function initialValues(prompt: ActionPrompt): Record<string, string> {
  switch (prompt.kind) {
    case 'assign':
      return { assignee: prompt.suggested };
    case 'route_override':
      return { route: '', reason: '' };
    case 'edit_fact':
      return {
        value: Array.isArray(prompt.value)
          ? prompt.value.join(', ')
          : prompt.value == null ? '' : String(prompt.value),
      };
    default:
      return { text: '' };
  }
}

/**
 * The forms a typed action opens before it does anything, and the panels that
 * show what it produced. Every one of them is a plain form with labelled
 * inputs — never a JSON editor (§33).
 */
export function ActionPromptDialog({
  prompt, busy, error, onSubmit, onClose,
}: {
  prompt: ActionPrompt;
  busy: boolean;
  error: string | null;
  onSubmit: (values: Record<string, string>) => void;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(() => initialValues(prompt));

  const config = {
    assign: {
      title: 'Assign owner',
      description: 'Who should take this work item on?',
      fields: [{ name: 'assignee', label: 'Owner', placeholder: 'Name or team', required: true, multiline: false }],
      submit: 'Assign',
    },
    note: {
      title: 'Add note',
      description: 'Recorded on this work item for whoever picks it up.',
      fields: [{ name: 'text', label: 'Note', placeholder: 'What should the next person know?', required: true, multiline: true }],
      submit: 'Add note',
    },
    route_override: {
      title: 'Change route',
      description: `Currently handled by ${prompt.kind === 'route_override' ? prompt.current : ''}. Your change is recorded as your decision, not the system's.`,
      fields: [
        { name: 'route', label: 'Send to', placeholder: 'Team or queue', required: true, multiline: false },
        { name: 'reason', label: 'Reason', placeholder: 'Why this belongs elsewhere', required: false, multiline: true },
      ],
      submit: 'Change route',
    },
    edit_fact: {
      title: `Edit ${prompt.kind === 'edit_fact' ? prompt.label.toLowerCase() : ''}`,
      description: 'Corrections are recorded, and anything worked out from the old value is flagged for rechecking.',
      fields: [{ name: 'value', label: prompt.kind === 'edit_fact' ? prompt.label : 'Value', placeholder: 'Leave empty for "not stated"', required: false, multiline: false }],
      submit: 'Save',
    },
  }[prompt.kind];

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit(values);
  }

  return (
    <Modal title={config.title} description={config.description} onClose={onClose}>
      <form onSubmit={submit} className="flex flex-col gap-4 p-4">
        {config.fields.map(field => (
          <label key={field.name} className="flex flex-col gap-1 text-sm">
            <span className="font-medium text-ink-700">{field.label}</span>
            {field.multiline ? (
              <textarea
                value={values[field.name] ?? ''}
                onChange={event => setValues({ ...values, [field.name]: event.target.value })}
                placeholder={field.placeholder}
                required={field.required}
                rows={3}
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            ) : (
              <input
                value={values[field.name] ?? ''}
                onChange={event => setValues({ ...values, [field.name]: event.target.value })}
                placeholder={field.placeholder}
                required={field.required}
                autoFocus
                className="rounded-md border border-slate-300 px-3 py-2 text-sm"
              />
            )}
          </label>
        ))}
        {error && <p className="text-sm text-bad">{error}</p>}
        <div className="flex items-center justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-md border border-slate-300 px-3 py-1.5 text-sm text-ink-700 hover:bg-slate-50">
            Cancel
          </button>
          <button
            type="submit"
            disabled={busy}
            className="rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50"
          >
            {busy ? 'Working…' : config.submit}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** What a completed action produced — a draft, a record, or a confirmation. */
export function ActionOutcomeDialog({ outcome, onClose }: { outcome: ActionOutcome; onClose: () => void }) {
  if (outcome.kind === 'message') {
    return (
      <Modal title="Done" onClose={onClose}>
        <p className="p-4 text-sm text-ink-900">{outcome.text}</p>
      </Modal>
    );
  }

  const result = outcome.result;
  if (result.kind === 'clarification_draft') {
    return (
      <Modal title="Draft question for the customer" description={result.note} onClose={onClose}>
        <div className="flex flex-col gap-3 p-4">
          <div>
            <div className="text-xs text-ink-400">Subject</div>
            <div className="text-sm text-ink-900">{result.subject}</div>
          </div>
          <div>
            <div className="text-xs text-ink-400">Message</div>
            <p className="whitespace-pre-wrap text-sm text-ink-900">{result.body}</p>
          </div>
          {result.asks.length > 0 && (
            <div>
              <div className="text-xs text-ink-400">Asks for</div>
              <ul className="mt-1 list-disc pl-5 text-sm text-ink-700">
                {result.asks.map(ask => <li key={ask}>{ask}</li>)}
              </ul>
            </div>
          )}
          <p className="text-xs text-ink-400">
            Nothing was sent. Copy this into your reply once you're happy with it.
          </p>
        </div>
      </Modal>
    );
  }

  if (result.kind === 'record') {
    const rows = flattenRecord(result.data);
    return (
      <Modal title={`${result.record_kind} ${result.reference}`} onClose={onClose}>
        <div className="p-4">
          {rows.length === 0 ? (
            <p className="text-sm text-ink-500">The system of record returned nothing for this reference.</p>
          ) : (
            <dl className="grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {rows.map(([label, value]) => (
                <div key={label}>
                  <dt className="text-xs text-ink-400">{label}</dt>
                  <dd className="break-words text-sm text-ink-900">{value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </Modal>
    );
  }

  return (
    <Modal title="Done" onClose={onClose}>
      <p className="p-4 text-sm text-ink-900">
        {result.kind === 'note' ? 'Note added.' : `Route changed to ${result.override.route}.`}
      </p>
    </Modal>
  );
}

/**
 * A record from a system of record, as labelled rows rather than JSON.
 *
 * Only scalar leaves are shown, one level into any nested object or the first
 * item of a collection. Anything deeper is a payload, and printing it would
 * reintroduce exactly what this redesign removed.
 */
function flattenRecord(data: Record<string, unknown>, depth = 0): [string, string][] {
  const rows: [string, string][] = [];
  for (const [key, value] of Object.entries(data ?? {})) {
    if (key.startsWith('_')) continue;
    const label = key.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());
    if (value == null || value === '') {
      rows.push([label, '—']);
    } else if (typeof value === 'boolean') {
      rows.push([label, value ? 'Yes' : 'No']);
    } else if (typeof value === 'string' || typeof value === 'number') {
      rows.push([label, String(value)]);
    } else if (Array.isArray(value)) {
      const first = value[0];
      if (first && typeof first === 'object' && depth < 1) {
        rows.push(...flattenRecord(first as Record<string, unknown>, depth + 1));
      } else {
        rows.push([label, `${value.length} item${value.length === 1 ? '' : 's'}`]);
      }
    } else if (typeof value === 'object' && depth < 1) {
      rows.push(...flattenRecord(value as Record<string, unknown>, depth + 1));
    }
  }
  return rows;
}

/**
 * The technical layer, one level deeper (§47).
 *
 * This is the only place a business user sees raw model output, and they have
 * to open it by name. It is fetched on demand from its own endpoint, so the
 * default screen's payload never contains it at all (§5, §60).
 */
export function TechnicalDrawer({
  runId, activityId, onClose, onOpenCockpit,
}: {
  runId: string;
  activityId: string;
  onClose: () => void;
  onOpenCockpit: () => void;
}) {
  const [detail, setDetail] = useState<BusinessTechnicalDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showRaw, setShowRaw] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.businessTechnicalDetail(runId, activityId)
      .then(next => { if (!cancelled) setDetail(next); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, [runId, activityId]);

  return (
    <Modal
      title={detail ? `Technical details — ${detail.title}` : 'Technical details'}
      description="Nodes, model calls and raw output behind this activity."
      onClose={onClose}
      wide
    >
      <div className="flex flex-col gap-4 p-4">
        {error && <p className="text-sm text-bad">{error}</p>}
        {!detail && !error && <p className="text-sm text-ink-500">Loading…</p>}

        {detail && (
          <>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Nodes" value={String(detail.nodes.length)} />
              <Stat label="Rules" value={String(detail.technical?.rule_count ?? 0)} />
              <Stat label="AI calls" value={String(detail.technical?.ai_calls.length ?? 0)} />
              <Stat
                label="Duration"
                value={detail.technical?.duration_ms != null ? `${detail.technical.duration_ms} ms` : '—'}
              />
            </div>

            {(detail.technical?.ai_calls.length ?? 0) > 0 && (
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-400">Model calls</h3>
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead className="text-xs text-ink-400">
                      <tr>
                        <th className="py-1 pr-4 font-medium">Requested</th>
                        <th className="py-1 pr-4 font-medium">Selected</th>
                        <th className="py-1 pr-4 font-medium">Executed</th>
                        <th className="py-1 pr-4 font-medium">Latency</th>
                        <th className="py-1 pr-4 font-medium">Cost</th>
                        <th className="py-1 font-medium">Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.technical!.ai_calls.map((call, index) => (
                        <tr key={index} className="border-t border-slate-200">
                          <td className="py-1 pr-4 text-ink-500">{call.requested ?? '—'}</td>
                          <td className="py-1 pr-4 text-ink-500">{call.selected ?? '—'}</td>
                          <td className="py-1 pr-4 font-medium text-ink-900">{call.executed ?? '—'}</td>
                          <td className="py-1 pr-4 text-ink-500">
                            {call.latency_ms != null ? `${(call.latency_ms / 1000).toFixed(1)}s` : '—'}
                          </td>
                          <td className="py-1 pr-4 text-ink-500">
                            {call.cost_usd != null ? formatCost(call.cost_usd) : '—'}
                          </td>
                          <td className="py-1 text-ink-500">
                            {call.fallback ? (call.fallback_reason ?? 'Provider fallback') : (call.routing_reason ?? '—')}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </section>
            )}

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-400">
                Nodes ({detail.nodes.length})
              </h3>
              <ul className="mt-2 flex flex-col gap-2">
                {detail.nodes.map(node => (
                  <li key={node.node_id} className="rounded border border-slate-200 p-2 text-sm">
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-medium text-ink-900">{node.node_id}</span>
                      <span className="text-xs text-ink-400">
                        {node.type_name} · {node.status}
                        {node.duration_s != null && ` · ${node.duration_s.toFixed(2)}s`}
                      </span>
                    </div>
                    {node.error && <p className="mt-1 text-xs text-bad">{node.error}</p>}
                    <button
                      type="button"
                      onClick={() => setShowRaw(showRaw === node.node_id ? null : node.node_id)}
                      className="mt-1 text-xs font-medium text-accent-700 hover:underline"
                      aria-expanded={showRaw === node.node_id}
                    >
                      {showRaw === node.node_id ? 'Hide raw output' : 'Raw output'}
                    </button>
                    {showRaw === node.node_id && (
                      <pre className="mt-2 max-h-64 overflow-auto rounded bg-slate-900 p-2 text-[11px] leading-relaxed text-slate-100">
                        {JSON.stringify(node.output, null, 2)}
                      </pre>
                    )}
                  </li>
                ))}
              </ul>
            </section>

            <button
              type="button"
              onClick={onOpenCockpit}
              className="self-start rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:bg-slate-50"
            >
              Open in Cockpit
            </button>
          </>
        )}
      </div>
    </Modal>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 p-2">
      <div className="text-xs text-ink-400">{label}</div>
      <div className="text-sm font-medium text-ink-900">{value}</div>
    </div>
  );
}
