import { useState } from 'react';

import type {
  BusinessAction,
  BusinessActivityView,
  BusinessAttentionItem,
  BusinessAttachment,
  BusinessDecisionView,
  BusinessExplanation,
  BusinessFact,
  BusinessNextStep,
  BusinessProjection,
  BusinessRelatedRecord,
  BusinessStatusView,
  BusinessTimelineEntry,
  BusinessUnderstanding,
} from '../../../api/types';
import {
  ActionRow,
  Card,
  CheckList,
  FactGrid,
  ModelMeta,
  SourceBadge,
} from './primitives';

type Run = (action: BusinessAction) => void;

const TONE_STYLE = {
  progress: 'bg-accent-100 text-accent-700',
  attention: 'bg-warn/15 text-warn',
  blocked: 'bg-bad/15 text-bad',
  waiting: 'bg-warn/15 text-warn',
  done: 'bg-ok/15 text-ok',
  stopped: 'bg-slate-200 text-ink-700',
} as const;

/**
 * The first viewport: who, what, where it stands, and what can be done — so a
 * salesperson never has to scroll through execution events to understand the
 * situation (§2, §3).
 */
export function StatusHero({
  projection, actions, onRun, busyId, narrated,
}: {
  projection: BusinessProjection;
  actions: BusinessAction[];
  onRun: Run;
  busyId: string | null;
  narrated: boolean;
}) {
  const { work_item: item, business_status: status } = projection;
  return (
    <header className="flex-none border-b border-slate-200 bg-white px-6 py-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="text-xs uppercase tracking-wide text-ink-400">
            {item.type} · #{item.reference}
            {item.assigned_to && <> · Assigned to {item.assigned_to}</>}
          </div>
          <h1 className="mt-1 truncate text-2xl font-semibold text-ink-900">{item.title}</h1>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span
              className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-semibold ${TONE_STYLE[status.tone]}`}
            >
              {status.headline}
            </span>
            {status.attention_count > 0 && (
              <span className="inline-flex items-center rounded-full bg-warn/15 px-2.5 py-1 text-xs font-medium text-warn">
                {status.attention_count} {status.attention_count === 1 ? 'item needs' : 'items need'} attention
              </span>
            )}
            {narrated && status.narration_model && (
              <span className="text-[11px] text-ink-400">
                Summary written by AI · {status.narration_model}
              </span>
            )}
          </div>

          <p className="mt-3 max-w-3xl text-base text-ink-700">{status.summary}</p>
        </div>

        <div className="flex flex-none flex-col items-end gap-2">
          <ActionRow actions={actions} onRun={onRun} busyId={busyId} />
        </div>
      </div>
    </header>
  );
}

const SEVERITY = {
  blocking: { mark: '!', label: 'Blocking', style: 'border-bad/40 bg-bad/5', chip: 'bg-bad/15 text-bad' },
  warning: { mark: '!', label: 'Needs attention', style: 'border-warn/40 bg-warn/5', chip: 'bg-warn/15 text-warn' },
  info: { mark: 'i', label: 'Missing', style: 'border-slate-200 bg-white', chip: 'bg-slate-100 text-ink-600' },
} as const;

/**
 * Every gap, with the ways to close it (§6, §7). Ordered by the server: what
 * blocks first, then what somebody can act on right now.
 */
export function AttentionCenter({
  items, onRun, busyId,
}: {
  items: BusinessAttentionItem[];
  onRun: Run;
  busyId: string | null;
}) {
  if (!items.length) return null;
  return (
    <Card title={`Needs attention (${items.length})`}>
      <ul className="flex flex-col gap-3">
        {items.map(item => {
          const severity = SEVERITY[item.severity];
          return (
            <li key={item.id} className={`rounded-md border p-3 ${severity.style}`}>
              <div className="flex flex-wrap items-baseline gap-2">
                <span aria-hidden="true" className="font-semibold text-ink-500">{severity.mark}</span>
                <span className="font-medium text-ink-900">{item.title}</span>
                <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${severity.chip}`}>
                  {item.status_label}
                </span>
              </div>
              {item.detail && <p className="mt-1 text-sm text-ink-500">{item.detail}</p>}
              <div className="mt-2">
                <ActionRow actions={item.actions} onRun={onRun} busyId={busyId} size="sm" />
              </div>
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** The handling decision, made visually important (§19). */
export function DecisionCard({
  decision, onRun, busyId, explanation, explaining, onExplain,
}: {
  decision: BusinessDecisionView;
  onRun: Run;
  busyId: string | null;
  explanation: BusinessExplanation | null;
  explaining: boolean;
  onExplain: () => void;
}) {
  const [open, setOpen] = useState(false);
  const supporting = decision.facts.filter(fact => !fact.missing);

  return (
    <Card title="Handling decision">
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="text-xl font-semibold text-ink-900">{decision.headline}</span>
        <SourceBadge source={decision.source} label={decision.source_label} />
        {decision.overridden && decision.original_headline && (
          <span className="text-xs text-ink-500">
            was {decision.original_headline}
            {decision.overridden_by ? ` · changed by ${decision.overridden_by}` : ''}
          </span>
        )}
      </div>
      {decision.summary && <p className="mt-1 text-sm text-ink-500">{decision.summary}</p>}
      {decision.reason && <p className="mt-2 text-sm text-ink-700">{decision.reason}</p>}

      {supporting.length > 0 && (
        <div className="mt-3">
          <CheckList items={supporting.slice(0, 6).map(fact => `${fact.label}: ${fact.display}`)} />
        </div>
      )}

      <div className="mt-4">
        <ActionRow
          actions={decision.actions}
          busyId={busyId}
          onRun={action => {
            if (action.type === 'explain_decision') {
              setOpen(value => !value);
              if (!explanation) onExplain();
              return;
            }
            onRun(action);
          }}
          size="sm"
        />
      </div>

      {open && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3">
          {explaining && <p className="text-sm text-ink-500">Putting this into words…</p>}
          {explanation?.summary && (
            <p className="text-sm text-ink-900">{explanation.summary}</p>
          )}
          <div className="mt-3 grid gap-4 sm:grid-cols-2">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-400">Facts</h3>
              <dl className="mt-1.5 flex flex-col gap-1 text-sm">
                {(explanation?.facts ?? []).map(fact => (
                  <div key={fact.id} className="flex justify-between gap-3">
                    <dt className="text-ink-500">{fact.label}</dt>
                    <dd className="text-right text-ink-900">{fact.value}</dd>
                  </div>
                ))}
              </dl>
            </div>
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-ink-400">Rules</h3>
              <ul className="mt-1.5 flex flex-col gap-1.5 text-sm">
                {(explanation?.rules ?? []).map(rule => (
                  <li key={rule.id}>
                    <div className="text-ink-900">{rule.name}</div>
                    {rule.description && <div className="text-xs text-ink-500">{rule.description}</div>}
                  </li>
                ))}
              </ul>
            </div>
          </div>
          {explanation?.source === 'ai' && explanation.model && (
            <p className="mt-3 text-[11px] text-ink-400">Wording by AI · {explanation.model}</p>
          )}
        </div>
      )}
    </Card>
  );
}

/** "What I understood" — business fields, never JSON (§4). */
export function UnderstandingCard({
  understanding, onRun, onEditFact, busyId,
}: {
  understanding: BusinessUnderstanding;
  onRun: Run;
  onEditFact: (action: BusinessAction, fact: BusinessFact) => void;
  busyId: string | null;
}) {
  if (!understanding.fields.length) return null;
  return (
    <Card
      title="What I understood"
      action={
        understanding.ai?.executed ? (
          <div className="flex flex-none items-center gap-2">
            <SourceBadge source="ai" label={understanding.source_label} />
            <ModelMeta ai={understanding.ai} />
          </div>
        ) : undefined
      }
    >
      {understanding.summary && <p className="mb-3 text-sm text-ink-700">{understanding.summary}</p>}
      <FactGrid facts={understanding.fields} onRun={onEditFact} showSource={false} />
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <ActionRow actions={understanding.actions} onRun={onRun} busyId={busyId} size="sm" />
        {understanding.confidence != null && (
          <span className="text-xs text-ink-400">
            Interpretation confidence {Math.round(understanding.confidence * 100)}%
          </span>
        )}
      </div>
    </Card>
  );
}

/** "What happens next" — always answered, blocked or not (§30). */
export function NextStepCard({
  nextStep, onRun, busyId,
}: {
  nextStep: BusinessNextStep;
  onRun: Run;
  busyId: string | null;
}) {
  return (
    <Card title="What happens next" tone={nextStep.blocked ? 'attention' : 'primary'}>
      <p className="text-base font-medium text-ink-900">{nextStep.headline}</p>
      {nextStep.blocked && nextStep.blocked_reason && (
        <p className="mt-1 text-sm text-ink-700">{nextStep.blocked_reason}</p>
      )}
      {nextStep.description && <p className="mt-1 text-sm text-ink-500">{nextStep.description}</p>}
      <div className="mt-3">
        <ActionRow actions={nextStep.actions} onRun={onRun} busyId={busyId} size="sm" />
      </div>
    </Card>
  );
}

/** "What you can do" — recommended first, alternatives visible (§29). */
export function ActionCenter({
  recommended, other, onRun, busyId,
}: {
  recommended: BusinessAction[];
  other: BusinessAction[];
  onRun: Run;
  busyId: string | null;
}) {
  if (!recommended.length && !other.length) return null;
  return (
    <Card title="What you can do">
      {recommended.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-ink-500">Recommended</h3>
          <div className="mt-1.5">
            <ActionRow
              actions={recommended.map(action => ({ ...action, emphasis: 'primary' as const }))}
              onRun={onRun}
              busyId={busyId}
              size="sm"
            />
          </div>
        </div>
      )}
      {other.length > 0 && (
        <div className={recommended.length ? 'mt-3' : ''}>
          <h3 className="text-xs font-medium text-ink-500">Other actions</h3>
          <div className="mt-1.5">
            <ActionRow actions={other} onRun={onRun} busyId={busyId} size="sm" />
          </div>
        </div>
      )}
    </Card>
  );
}

/**
 * How much execution this one business activity collapsed (§10).
 *
 * Built as a single string rather than interpolated JSX so it reads as one
 * phrase to a screen reader — and to a test.
 */
function technicalSummary(activity: BusinessActivityView): string {
  const nodes = activity.technical.node_ids.length;
  const parts = [`${nodes} technical ${nodes === 1 ? 'step' : 'steps'}`];
  if (activity.technical.rule_count > 0) parts.push(`${activity.technical.rule_count} rules`);
  return parts.join(' · ');
}

const ACTIVITY_MARK = {
  completed: '✓',
  active: '●',
  attention: '!',
  planned: '○',
  skipped: '·',
} as const;

/**
 * Business activities, expandable into their findings (§26). Completed work
 * recedes; anything needing attention does not (§41).
 */
export function ActivityList({
  activities, summary, onRun, busyId, onTechnical,
}: {
  activities: BusinessActivityView[];
  summary: Record<string, number>;
  onRun: Run;
  busyId: string | null;
  onTechnical: (activityId: string) => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (!activities.length) return null;

  return (
    <Card
      title="What happened"
      subtitle={
        summary.total
          ? `${summary.completed} of ${summary.total} business activities completed`
          : undefined
      }
    >
      <ul className="flex flex-col gap-2">
        {activities.map(activity => {
          const open = expanded === activity.id;
          return (
            <li
              key={activity.id}
              className={`rounded-md border p-3 ${
                activity.status === 'attention' ? 'border-bad/40 bg-bad/5' : 'border-slate-200 bg-white'
              }`}
            >
              <button
                type="button"
                onClick={() => setExpanded(open ? null : activity.id)}
                aria-expanded={open}
                className="flex w-full items-start justify-between gap-3 text-left"
              >
                <span className="flex min-w-0 items-baseline gap-2">
                  <span aria-hidden="true" className="text-ink-400">{ACTIVITY_MARK[activity.status]}</span>
                  <span className="min-w-0">
                    <span className="font-medium text-ink-900">{activity.title}</span>
                    <span className="ml-2 text-xs text-ink-400">{activity.status_label}</span>
                    {activity.summary && (
                      <span className="block text-sm text-ink-500">{activity.summary}</span>
                    )}
                  </span>
                </span>
                <span className="flex flex-none items-center gap-2">
                  <SourceBadge
                    source={activity.kind === 'mixed' ? 'workflow' : activity.kind}
                    label={activity.kind_label}
                  />
                  <span aria-hidden="true" className="text-xs text-ink-400">{open ? '▲' : '▼'}</span>
                </span>
              </button>

              {open && (
                <div className="mt-3 border-t border-slate-200 pt-3">
                  <FactGrid facts={activity.facts} />
                  <div className="mt-3 flex flex-wrap items-center gap-3">
                    <ActionRow
                      actions={activity.actions}
                      busyId={busyId}
                      size="sm"
                      onRun={action => {
                        if (action.type === 'open_technical_details') {
                          onTechnical(activity.id);
                          return;
                        }
                        onRun(action);
                      }}
                    />
                    {activity.ai && <ModelMeta ai={activity.ai} />}
                    <span className="text-[11px] text-ink-400">
                      {technicalSummary(activity)}
                    </span>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

const TIMELINE_STYLE = {
  activity: 'bg-accent-500',
  human: 'bg-warn',
  failure: 'bg-bad',
  edit: 'bg-warn',
  status: 'bg-ink-400',
  override: 'bg-warn',
} as const;

function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime())
    ? ts
    : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

/** The business timeline (§9) — one entry per thing that happened. */
export function BusinessTimeline({ entries }: { entries: BusinessTimelineEntry[] }) {
  const [open, setOpen] = useState(false);
  if (!entries.length) {
    return <p className="text-sm text-ink-500">History will appear here as this work progresses.</p>;
  }
  const shown = open ? entries.slice().reverse() : entries.slice().reverse().slice(0, 5);

  return (
    <div>
      <ol className="flex flex-col gap-3">
        {shown.map(entry => (
          <li key={entry.id} className="flex items-start gap-3 text-sm">
            <span
              aria-hidden="true"
              className={`mt-1.5 h-2 w-2 flex-none rounded-full ${TIMELINE_STYLE[entry.kind]}`}
            />
            <div className="min-w-0">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-ink-900">{entry.title}</span>
                <span className="text-xs text-ink-400">{formatTime(entry.ts)}</span>
              </div>
              {entry.detail && <div className="text-sm text-ink-500">{entry.detail}</div>}
              {entry.marks.length > 0 && (
                <ul className="mt-1 flex flex-col gap-0.5">
                  {entry.marks.map(mark => (
                    <li key={mark} className="text-xs text-ink-500">
                      <span aria-hidden="true" className="text-ok">✓</span> {mark}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
      </ol>
      {entries.length > 5 && (
        <button
          type="button"
          onClick={() => setOpen(value => !value)}
          className="mt-3 text-sm font-medium text-accent-700 hover:underline"
        >
          {open ? 'Show less' : `View full history (${entries.length})`}
        </button>
      )}
    </div>
  );
}

/** High-value context: customer, related records, attachments, owner (§35). */
export function ContextSidebar({
  projection, onRun, busyId,
}: {
  projection: BusinessProjection;
  onRun: Run;
  busyId: string | null;
}) {
  const { work_item: item, related_records: records, attachments } = projection;
  const keyFacts = projection.understanding.fields.filter(fact => !fact.missing).slice(0, 5);

  return (
    <div className="flex flex-col gap-4">
      {item.customer && (
        <Card title="Customer">
          <p className="text-base font-medium text-ink-900">{item.customer}</p>
        </Card>
      )}

      {records.length > 0 && (
        <Card title="Related records">
          <ul className="flex flex-col gap-3">
            {records.map((record: BusinessRelatedRecord) => (
              <li key={record.id}>
                <div className="text-xs text-ink-400">{record.label}</div>
                <div className="text-sm text-ink-900">{record.reference}</div>
                <div className="mt-1.5">
                  <ActionRow actions={record.actions} onRun={onRun} busyId={busyId} size="sm" />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      {attachments.length > 0 && (
        <Card title="Attachments">
          <ul className="flex flex-col gap-3">
            {attachments.map((attachment: BusinessAttachment) => (
              <li key={attachment.id}>
                <div className="truncate text-sm text-ink-900">{attachment.name}</div>
                <div className="mt-1.5">
                  <ActionRow actions={attachment.actions} onRun={onRun} busyId={busyId} size="sm" />
                </div>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <Card title="Owner">
        <p className="text-sm text-ink-900">
          {item.assigned_to ?? projection.decision?.headline ?? 'Not assigned'}
        </p>
      </Card>

      {keyFacts.length > 0 && (
        <Card title="Key facts" tone="quiet">
          <FactGrid facts={keyFacts} columns={1} showSource={false} />
        </Card>
      )}
    </div>
  );
}

export function StatusSummary({ status }: { status: BusinessStatusView }) {
  return (
    <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
      {status.headline}. {status.summary}
      {status.attention_count > 0 && ` ${status.attention_count} items need attention.`}
    </div>
  );
}
