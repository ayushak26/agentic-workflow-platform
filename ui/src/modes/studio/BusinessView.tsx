import { useEffect, useMemo, useState, type FormEvent } from 'react';

import { api } from '../../api/client';
import type { BusinessProjection, BusinessTimelineEntry } from '../../api/types';
import { HITLPanel } from './HITLPanel';
import { OutputViewer } from './OutputViewer';
import { AskAiPanel } from './run-history/AskAiPanel';
import { useCockpitRun } from './cockpit/useCockpitRun';
import { startRetryRun } from './cockpit/node-render';
import { collectGuidedArtifacts } from './guided/runtime-model';

const PROGRESS_MARK: Record<BusinessProjection['progress'][number]['state'], string> = {
  completed: '✓',
  active: '●',
  attention: '!',
  planned: '○',
  skipped: '·',
};

function useBusinessProjection(
  runId: string | undefined,
  significantEventCount: number,
  gateNodeId: string | undefined,
  finishedStatus: string | undefined,
) {
  const [projection, setProjection] = useState<BusinessProjection | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [manualRefetchCount, setManualRefetchCount] = useState(0);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    api.businessProjection(runId)
      .then(next => { if (!cancelled) setProjection(next); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
    // significantEventCount/gateNodeId/finishedStatus are refetch triggers,
    // not values read in the body — the projection is always re-derived
    // from the run's current durable state on the server.
  }, [runId, significantEventCount, gateNodeId, finishedStatus, manualRefetchCount]);

  return { projection, error, refetch: () => setManualRefetchCount(n => n + 1) };
}

function formatTimelineTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function ProgressChecklist({ progress }: { progress: BusinessProjection['progress'] }) {
  if (progress.length === 0) return null;
  return (
    <ol className="flex flex-col gap-1.5" aria-label="Progress">
      {progress.map(stage => (
        <li key={stage.id} className="flex items-center gap-2 text-sm">
          <span
            aria-hidden="true"
            className={`inline-flex h-5 w-5 flex-none items-center justify-center rounded-full text-xs font-semibold ${
              stage.state === 'completed' ? 'bg-ok/15 text-ok'
              : stage.state === 'active' ? 'bg-accent-100 text-accent-700'
              : stage.state === 'attention' ? 'bg-bad/15 text-bad'
              : 'bg-slate-100 text-ink-400'
            }`}
          >
            {PROGRESS_MARK[stage.state]}
          </span>
          <span className={stage.state === 'planned' ? 'text-ink-400' : 'text-ink-900'}>
            {stage.display_name}
          </span>
          {stage.total_count > 1 && (
            <span className="text-xs text-ink-400">{stage.completed_count}/{stage.total_count}</span>
          )}
        </li>
      ))}
    </ol>
  );
}

function formatFactDisplay(value: unknown): string {
  if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function FactEditor({
  field, value, busy, onCancel, onSubmit,
}: {
  field: string;
  value: unknown;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (rawValue: string, originalType: string) => void;
}) {
  const originalType = typeof value;
  const initial = Array.isArray(value) ? value.join(', ') : value == null ? '' : String(value);
  const [draft, setDraft] = useState(initial);

  if (originalType === 'boolean') {
    return (
      <div className="mt-1 flex items-center gap-2">
        <button type="button" disabled={busy} onClick={() => onSubmit('true', 'boolean')}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50">
          Yes
        </button>
        <button type="button" disabled={busy} onClick={() => onSubmit('false', 'boolean')}
          className="rounded-md border border-slate-300 px-2 py-1 text-xs hover:bg-slate-50 disabled:opacity-50">
          No
        </button>
        <button type="button" onClick={onCancel} className="text-xs text-ink-400 hover:underline">Cancel</button>
      </div>
    );
  }

  return (
    <div className="mt-1 flex items-center gap-2">
      <input
        value={draft}
        onChange={e => setDraft(e.target.value)}
        placeholder={field === 'request_types' ? 'comma-separated' : undefined}
        autoFocus
        className="w-full rounded-md border border-slate-300 px-2 py-1 text-sm"
      />
      <button type="button" disabled={busy} onClick={() => onSubmit(draft, originalType)}
        className="flex-none rounded-md bg-accent-600 px-2 py-1 text-xs font-medium text-white hover:bg-accent-700 disabled:opacity-50">
        {busy ? 'Saving…' : 'Save'}
      </button>
      <button type="button" onClick={onCancel} className="flex-none text-xs text-ink-400 hover:underline">Cancel</button>
    </div>
  );
}

function Timeline({ entries }: { entries: BusinessTimelineEntry[] }) {
  if (entries.length === 0) {
    return <p className="text-sm text-ink-500">History will appear here as this work progresses.</p>;
  }
  return (
    <ol className="flex flex-col gap-2">
      {entries.slice().reverse().map((entry, index) => (
        <li key={`${entry.ts}-${index}`} className="flex items-start gap-3 text-sm">
          <span className="mt-1 h-1.5 w-1.5 flex-none rounded-full bg-accent-500" aria-hidden="true" />
          <div>
            <div className="text-ink-900">{entry.label}</div>
            <div className="text-xs text-ink-400">{formatTimelineTime(entry.ts)}</div>
          </div>
        </li>
      ))}
    </ol>
  );
}

export function BusinessView() {
  const run = useCockpitRun();
  const {
    runId, navState, navigate, triggerError, liveRun, gate, setGateHidden,
    gateFetchError, retryGateFetch, finished, events, streamError,
    applyResumeResult, setTriggerError, pipelineDoc, continueToNextStage,
    continuingStage, continueError,
  } = run;

  const [reviewOpen, setReviewOpen] = useState(false);
  const [fullOutputOpen, setFullOutputOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  const [controlBusy, setControlBusy] = useState(false);
  const [editingField, setEditingField] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [editBusy, setEditBusy] = useState(false);
  const [commandInput, setCommandInput] = useState('');
  const [commandBusy, setCommandBusy] = useState(false);
  const [commandFeedback, setCommandFeedback] = useState<{ text: string; kind: 'success' | 'error' } | null>(null);

  // The frontend's RunEvent union only carries node/run lifecycle events
  // (no per-token streaming events reach it), so every arrival here is
  // already "significant" — a fresh SSE event is a reasonable signal to
  // re-fetch the projection.
  const { projection, error: projectionError, refetch: refetchProjection } = useBusinessProjection(
    runId, events.length, gate?.nodeId, finished?.status,
  );

  const finishedState = finished?.state as Record<string, unknown> | undefined;
  const finishedOutputs = finishedState?.node_outputs;
  const outputs = useMemo(() => ({
    ...(liveRun?.outputs ?? {}),
    ...(finishedOutputs && typeof finishedOutputs === 'object' && !Array.isArray(finishedOutputs)
      ? finishedOutputs as Record<string, unknown>
      : {}),
  }), [finishedOutputs, liveRun?.outputs]);
  const artifacts = useMemo(() => collectGuidedArtifacts(outputs), [outputs]);
  const outputState = finished?.state ?? {
    node_outputs: outputs,
    inputs: liveRun?.inputs ?? navState.inputs ?? {},
    variables: liveRun?.variables ?? {},
  };
  const workflowName = navState.workflowName ?? liveRun?.workflow_name ?? projection?.process.name ?? 'Work item';
  const pipelineHasNext = Boolean(
    pipelineDoc && pipelineDoc.current_stage_index + 1 < pipelineDoc.stages.length
    && finished?.status === 'completed',
  );

  if (!runId) return <div className="p-8 text-ink-500">No run was selected.</div>;
  if (!projection) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center" role="status">
        <span className="h-6 w-6 animate-spin rounded-full border-2 border-accent-400 border-t-transparent" aria-hidden="true" />
        <h2 className="text-lg font-semibold text-ink-900">Opening this work item</h2>
        <p className="text-sm text-ink-500">Loading the latest status.</p>
        {(projectionError || triggerError) && (
          <p className="text-sm text-bad">{projectionError ?? triggerError}</p>
        )}
      </div>
    );
  }

  const approvalAction = projection.required_user_actions.find(action => action.type === 'approval_review');
  const resumeAction = projection.required_user_actions.find(action => action.type === 'resume_decision');
  const canControl = (control: string) => projection.allowed_controls.includes(control as never);

  // A closed, validated set of commands — deliberately not a free-form agent
  // that could take arbitrary action. Anything else falls through to the
  // read-only "Ask about this work" chat.
  async function runCommand(raw: string): Promise<{ text: string; kind: 'success' | 'error' }> {
    if (!runId) return { text: 'No run selected.', kind: 'error' };
    const [cmd, ...rest] = raw.slice(1).trim().split(/\s+/);
    const arg = rest.join(' ');
    const name = cmd.toLowerCase();

    if (name === 'help') {
      return { text: 'Commands: /pause, /resume, /approve, /reject <reason>, /assign <name>.', kind: 'success' };
    }
    if (name === 'pause') {
      if (!canControl('pause')) return { text: "This work item can't be paused right now.", kind: 'error' };
      await api.pauseRun(runId);
      return { text: 'Paused.', kind: 'success' };
    }
    if (name === 'resume') {
      if (!canControl('resume')) return { text: "There's nothing to resume right now.", kind: 'error' };
      applyResumeResult(await api.resumePausedRun(runId));
      return { text: 'Resumed.', kind: 'success' };
    }
    if (name === 'approve' || name === 'reject') {
      if (!approvalAction) return { text: "There's nothing waiting for approval right now.", kind: 'error' };
      if (name === 'reject' && !arg) return { text: 'A reason helps — try /reject <reason>.', kind: 'error' };
      setGateHidden(true);
      applyResumeResult(await api.resumeWorkflow(
        runId, name === 'reject' ? { decision: 'reject', reason: arg } : { decision: 'approve' },
      ));
      return { text: name === 'approve' ? 'Approved.' : `Rejected: ${arg}`, kind: 'success' };
    }
    if (name === 'assign') {
      if (!arg) return { text: 'Usage: /assign <name>', kind: 'error' };
      await api.assignRun(runId, arg);
      refetchProjection();
      return { text: `Assigned to ${arg}.`, kind: 'success' };
    }
    return { text: `Unknown command "/${cmd}". Try /help.`, kind: 'error' };
  }

  async function submitCommand(e: FormEvent) {
    e.preventDefault();
    const trimmed = commandInput.trim();
    if (!trimmed) return;
    if (!trimmed.startsWith('/')) {
      setAskOpen(true);
      return;
    }
    setCommandBusy(true);
    setCommandFeedback(null);
    try {
      const outcome = await runCommand(trimmed);
      setCommandFeedback(outcome);
      if (outcome.kind === 'success') setCommandInput('');
    } catch (e2) {
      setCommandFeedback({ text: e2 instanceof Error ? e2.message : String(e2), kind: 'error' });
    } finally {
      setCommandBusy(false);
    }
  }

  async function handlePause() {
    if (!runId) return;
    setControlBusy(true);
    setControlError(null);
    try {
      await api.pauseRun(runId);
    } catch (e) {
      setControlError(e instanceof Error ? e.message : String(e));
    } finally {
      setControlBusy(false);
    }
  }

  async function handleResume() {
    if (!runId) return;
    setControlBusy(true);
    setControlError(null);
    try {
      const result = await api.resumePausedRun(runId);
      applyResumeResult(result);
    } catch (e) {
      setControlError(e instanceof Error ? e.message : String(e));
    } finally {
      setControlBusy(false);
    }
  }

  async function handleStop() {
    if (!runId) return;
    // deleteRun is the only "stop" primitive today — there is no
    // cancel-in-place for a running workflow, so this is genuinely
    // destructive. Say so rather than dressing it up as a soft pause.
    if (!window.confirm('This permanently stops and deletes this work item and its history. Continue?')) {
      return;
    }
    setControlBusy(true);
    setControlError(null);
    try {
      await api.deleteRun(runId);
      navigate('/history');
    } catch (e) {
      setControlError(e instanceof Error ? e.message : String(e));
      setControlBusy(false);
    }
  }

  function handleRetry() {
    if (!liveRun) return;
    const error = startRetryRun(liveRun, navigate, 'business');
    setControlError(error);
  }

  function openCockpit() {
    navigate(`/cockpit/${runId}`, {
      state: {
        attach: true,
        workflowYaml: navState.workflowYaml ?? liveRun?.workflow_yaml,
        workflowName,
      },
    });
  }

  const understandingEntries = projection.understanding.result && typeof projection.understanding.result === 'object'
    ? Object.entries(projection.understanding.result as Record<string, unknown>)
    : [];
  const editableFacts = new Set(projection.editable_facts ?? []);
  const staleDecisions = new Set(projection.stale_decisions ?? []);
  const staleDecisionsInThisDecision = projection.decision != null
    && Object.keys(projection.decision.decisions).some(key => staleDecisions.has(key));

  async function submitFactCorrection(field: string, rawValue: unknown, originalType: string) {
    setEditBusy(true);
    setEditError(null);
    try {
      let value: unknown;
      if (originalType === 'boolean') {
        value = rawValue === 'true';
      } else if (field === 'request_types') {
        value = (rawValue as string).split(',').map(s => s.trim()).filter(Boolean);
      } else {
        value = rawValue === '' ? null : rawValue;
      }
      await api.correctFact(runId!, field, value);
      setEditingField(null);
      refetchProjection();
    } catch (e) {
      setEditError(e instanceof Error ? e.message : String(e));
    } finally {
      setEditBusy(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-slate-50">
      <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {projection.work_item.status}. {projection.current_activity?.message ?? ''}
      </div>

      {/* Work Item header — §26 */}
      <header className="flex-none border-b border-slate-200 bg-white px-6 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="text-xs uppercase tracking-wide text-ink-400">
              {projection.work_item.type} · #{runId.slice(0, 8)}
            </div>
            <h1 className="mt-1 text-xl font-semibold text-ink-900">{workflowName}</h1>
            <div className="mt-1 flex items-center gap-2 text-sm">
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${
                projection.status === 'completed' ? 'bg-ok/15 text-ok'
                : projection.status === 'failed' ? 'bg-bad/15 text-bad'
                : projection.status === 'paused' ? 'bg-warn/15 text-warn'
                : 'bg-accent-100 text-accent-700'
              }`}>
                {projection.work_item.status}
              </span>
              <span className="text-ink-400">{projection.process.goal}</span>
              {projection.work_item.assigned_to && (
                <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-ink-700">
                  Assigned to {projection.work_item.assigned_to}
                </span>
              )}
            </div>
          </div>
          <div className="flex flex-none items-center gap-2">
            {canControl('pause') && (
              <button type="button" onClick={handlePause} disabled={controlBusy}
                className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-ink-700 hover:bg-slate-50 disabled:opacity-50">
                Pause
              </button>
            )}
            {canControl('resume') && (
              <button type="button" onClick={handleResume} disabled={controlBusy}
                className="rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">
                Resume
              </button>
            )}
            {canControl('retry') && (
              <button type="button" onClick={handleRetry} disabled={!liveRun?.retry_available}
                className="rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">
                Retry safely
              </button>
            )}
            {approvalAction && (
              <button type="button" onClick={() => setReviewOpen(true)}
                className="rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700">
                Review now
              </button>
            )}
            {canControl('stop') && (
              <button type="button" onClick={handleStop} disabled={controlBusy}
                className="rounded-md border border-bad/40 px-3 py-1.5 text-sm font-medium text-bad hover:bg-bad/5 disabled:opacity-50">
                Stop
              </button>
            )}
            <button type="button" onClick={openCockpit}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-ink-500 hover:bg-slate-50">
              Technical details
            </button>
          </div>
        </div>
        {controlError && <p className="mt-2 text-sm text-bad">{controlError}</p>}
      </header>

      <div className="grid flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        {/* Conversation + Activity */}
        <main className="flex flex-col gap-4">
          {approvalAction && (
            <section className="rounded-lg border border-warn/40 bg-warn/5 p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-warn">Approval required</div>
              <p className="mt-1 text-sm text-ink-900">{approvalAction.question || 'Confirm this work before it continues.'}</p>
              <button type="button" onClick={() => setReviewOpen(true)}
                className="mt-3 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700">
                Review and respond
              </button>
            </section>
          )}
          {resumeAction && !approvalAction && (
            <section className="rounded-lg border border-warn/40 bg-warn/5 p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-warn">Paused</div>
              <p className="mt-1 text-sm text-ink-900">{resumeAction.message}</p>
              <button type="button" onClick={handleResume} disabled={controlBusy}
                className="mt-3 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">
                Resume
              </button>
            </section>
          )}

          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">Current activity</div>
            <p className="mt-1 text-base text-ink-900">
              {projection.current_activity?.message
                ?? (projection.status === 'completed' ? 'All work is complete.' : 'Preparing the next activity.')}
            </p>
          </section>

          {understandingEntries.length > 0 && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">What I understood</div>
              <dl className="mt-2 grid grid-cols-2 gap-3 text-sm">
                {understandingEntries
                  .filter(([key, value]) => editableFacts.has(key) || (value != null && value !== ''))
                  .map(([key, value]) => {
                    const editable = editableFacts.has(key);
                    const isEditing = editingField === key;
                    return (
                      <div key={key}>
                        <dt className="flex items-center gap-1.5 text-xs text-ink-400">
                          {key.replace(/_/g, ' ')}
                          {editable && !isEditing && (
                            <button type="button" onClick={() => { setEditingField(key); setEditError(null); }}
                              className="font-medium text-accent-600 hover:underline"
                              aria-label={`Edit ${key.replace(/_/g, ' ')}`}>
                              edit
                            </button>
                          )}
                        </dt>
                        {isEditing ? (
                          <FactEditor
                            field={key}
                            value={value}
                            busy={editBusy}
                            onCancel={() => { setEditingField(null); setEditError(null); }}
                            onSubmit={(raw, type) => submitFactCorrection(key, raw, type)}
                          />
                        ) : (
                          <dd className="break-words text-ink-900">
                            {value == null || value === ''
                              ? <span className="italic text-ink-400">not stated</span>
                              : formatFactDisplay(value)}
                          </dd>
                        )}
                      </div>
                    );
                  })}
              </dl>
              {editError && <p className="mt-2 text-xs text-bad">{editError}</p>}
              {projection.understanding.confidence != null && (
                <p className="mt-2 text-xs text-ink-400">
                  Confidence: {Math.round((projection.understanding.confidence as number) * 100)}%
                </p>
              )}
            </section>
          )}

          {projection.missing_information.length > 0 && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">What I still need</div>
              <ul className="mt-2 list-disc pl-5 text-sm text-ink-900">
                {projection.missing_information.map(item => (
                  <li key={item}>{item.replace(/_/g, ' ')}</li>
                ))}
              </ul>
            </section>
          )}

          {projection.checks.length > 0 && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">What I checked</div>
              <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                {projection.checks.map(check => (
                  <li key={check.node_id} className="flex items-center gap-2">
                    <span aria-hidden="true">{check.status === 'done' || check.status === 'reused' ? '✓' : check.status === 'failed' ? '×' : '…'}</span>
                    <span className="text-ink-900">{check.display_name}</span>
                    {check.outcome && <span className="text-ink-400">— {check.outcome}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}

          {projection.decision && (
            <section className="rounded-lg border border-slate-200 bg-white p-4">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">How this was decided</div>
              <dl className="mt-2 grid grid-cols-2 gap-3 text-sm">
                {Object.entries(projection.decision.decisions).map(([key, value]) => (
                  <div key={key}>
                    <dt className="flex items-center gap-1.5 text-xs text-ink-400">
                      {key.replace(/_/g, ' ')}
                      {staleDecisions.has(key) && (
                        <span
                          title="Computed before a related fact was corrected — re-check before relying on it."
                          className="rounded-full bg-warn/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-warn"
                        >
                          Stale
                        </span>
                      )}
                    </dt>
                    <dd className="break-words text-ink-900">{formatFactDisplay(value)}</dd>
                  </div>
                ))}
              </dl>
              {staleDecisionsInThisDecision && (
                <p className="mt-2 text-xs text-ink-500">
                  A corrected fact may have changed one or more of these — use <strong>Retry safely</strong> to
                  recompute them.
                </p>
              )}
              <button type="button" onClick={() => setWhyOpen(v => !v)}
                className="mt-3 text-sm font-medium text-accent-700 hover:underline">
                {whyOpen ? 'Hide why' : 'Why?'}
              </button>
              {whyOpen && (
                <div className="mt-2 space-y-2 text-sm">
                  {projection.decision_explanation.length === 0 ? (
                    <p className="text-ink-500">No business rule matched — this was likely escalated by default.</p>
                  ) : projection.decision_explanation.map(entry => (
                    <div key={entry.name} className="rounded border border-slate-200 bg-slate-50 p-2">
                      <div className="font-medium text-ink-900">{entry.name}</div>
                      <div className="text-ink-500">{entry.description}</div>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">History</div>
            <div className="mt-2"><Timeline entries={projection.timeline} /></div>
          </section>

          {streamError && (
            <p className="text-sm text-ink-400">Live updates are reconnecting. This work continues in the background.</p>
          )}
          {gateFetchError && (
            <div className="rounded-lg border border-bad/30 bg-bad/5 p-3 text-sm">
              <p className="text-bad">We could not load the saved review question.</p>
              <button type="button" onClick={retryGateFetch} className="mt-1 font-medium text-accent-700 hover:underline">
                Try again
              </button>
            </div>
          )}
        </main>

        {/* Business Context */}
        <aside className="flex flex-col gap-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">Progress</div>
            <div className="mt-2"><ProgressChecklist progress={projection.progress} /></div>
          </section>

          <section className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex items-center justify-between">
              <div className="text-xs font-semibold uppercase tracking-wide text-ink-400">
                Deliverables{artifacts.length ? ` (${artifacts.length})` : ''}
              </div>
              <button type="button" onClick={() => setFullOutputOpen(true)} className="text-xs font-medium text-accent-700 hover:underline">
                View all
              </button>
            </div>
            {artifacts.length === 0 ? (
              <p className="mt-2 text-sm text-ink-400">Deliverables will appear here as they're produced.</p>
            ) : (
              <ul className="mt-2 flex flex-col gap-1.5 text-sm">
                {artifacts.slice(0, 4).map(artifact => (
                  <li key={artifact.key} className="flex items-center justify-between gap-2">
                    <span className="truncate text-ink-900">{artifact.label}</span>
                    <button type="button" onClick={() => api.downloadArtifact(artifact.key).catch(e => setTriggerError(String(e)))}
                      className="flex-none text-xs font-medium text-accent-700 hover:underline">
                      Download
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {pipelineHasNext && (
            <section className="rounded-lg border border-accent-200 bg-accent-50 p-4">
              <p className="text-sm font-medium text-ink-900">This stage is complete.</p>
              <p className="text-sm text-ink-500">The next stage in this process is ready to start.</p>
              <button type="button" onClick={continueToNextStage} disabled={continuingStage}
                className="mt-2 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">
                {continuingStage ? 'Starting next stage…' : 'Continue to next stage'}
              </button>
              {continueError && <p className="mt-1 text-sm text-bad">{continueError}</p>}
            </section>
          )}
        </aside>
      </div>

      {/* Ask bar — a question opens the full chat; a leading "/" runs one of
          a fixed, validated set of commands instead (never a free-form agent). */}
      <footer className="flex-none border-t border-slate-200 bg-white px-6 py-3">
        <form onSubmit={submitCommand} className="flex items-center gap-2">
          <input
            value={commandInput}
            onChange={e => setCommandInput(e.target.value)}
            disabled={commandBusy}
            placeholder="Ask about this work, or type a command — /pause, /approve, /assign Maria…"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-400 disabled:opacity-50"
          />
          <button type="submit" disabled={commandBusy || !commandInput.trim()}
            className="flex-none rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50">
            {commandBusy ? 'Working…' : 'Send'}
          </button>
          <button type="button" onClick={() => setAskOpen(true)}
            className="flex-none text-sm font-medium text-accent-700 hover:underline">
            Full chat
          </button>
        </form>
        {commandFeedback && (
          <p className={`mt-1.5 text-xs ${commandFeedback.kind === 'error' ? 'text-bad' : 'text-ink-500'}`}>
            {commandFeedback.text}
          </p>
        )}
      </footer>

      {gate && reviewOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Review required">
          <div className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white">
            <HITLPanel
              key={`${runId}:${gate.nodeId}`}
              runId={runId}
              pausedNodeId={gate.nodeId}
              pausedStepName={projection.current_activity?.display_name}
              reviewPurpose={approvalAction?.question}
              question={gate.question}
              context={gate.context}
              allowedActions={gate.allowedActions}
              content={gate.content}
              allowDocumentOverride={gate.allowDocumentOverride}
              maxEditChars={gate.maxEditChars}
              onResult={applyResumeResult}
              onSubmitting={() => { setGateHidden(true); setReviewOpen(false); }}
              onSubmitError={message => setTriggerError(message)}
              onClose={() => setReviewOpen(false)}
            />
          </div>
        </div>
      )}

      {fullOutputOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Detailed output workspace">
          <div className="flex max-h-[90vh] w-full max-w-4xl flex-col overflow-hidden rounded-lg bg-white">
            <div className="flex flex-none items-center justify-between border-b border-slate-200 px-4 py-3">
              <div className="text-sm font-semibold text-ink-900">Detailed output workspace</div>
              <button type="button" onClick={() => setFullOutputOpen(false)} className="text-sm text-ink-500 hover:text-ink-900">Close</button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <OutputViewer runId={runId} state={outputState} workflowName={workflowName} />
            </div>
          </div>
        </div>
      )}

      {askOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="dialog" aria-modal="true" aria-label="Ask about this work">
          <div className="flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-lg bg-white">
            <div className="flex flex-none items-center justify-between border-b border-slate-200 px-4 py-3">
              <div>
                <div className="text-sm font-semibold text-ink-900">Ask about this work</div>
                <div className="text-xs text-ink-400">Answers only what's recorded on this run — it can't take action yet.</div>
              </div>
              <button type="button" onClick={() => setAskOpen(false)} className="text-sm text-ink-500 hover:text-ink-900">Close</button>
            </div>
            <div className="flex-1 overflow-y-auto">
              <AskAiPanel runId={runId} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
