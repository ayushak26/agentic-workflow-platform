import { useEffect, useMemo, useState } from 'react';

import { api } from '../../api/client';
import type { RunEvent } from '../../api/types';
import { HITLPanel } from './HITLPanel';
import { OutputViewer } from './OutputViewer';
import { useCockpitRun } from './cockpit/useCockpitRun';
import {
  startRetryRun,
  suggestedCorrectiveAction,
} from './cockpit/node-render';
import {
  buildGuidedRuntimeModel,
  collectGuidedArtifacts,
  GUIDED_STATUS_LABEL,
  nodeStatusesFromRun,
  type GuidedStage,
  type GuidedStep,
} from './guided/runtime-model';

type Emphasis = 'administrator' | 'expert';
type GuidedSection = 'overview' | 'outputs' | 'activity';

const RUN_STATUS: Record<string, { label: string; className: string; symbol: string }> = {
  connecting: { label: 'Connecting', className: 'is-running', symbol: '↻' },
  pending: { label: 'Preparing', className: 'is-running', symbol: '•' },
  running: { label: 'In progress', className: 'is-running', symbol: '●' },
  paused: { label: 'Waiting for you', className: 'is-attention', symbol: '!' },
  completed: { label: 'Completed', className: 'is-complete', symbol: '✓' },
  rejected: { label: 'Stopped after review', className: 'is-attention', symbol: '!' },
  failed: { label: 'Needs attention', className: 'is-error', symbol: '×' },
  cancelled: { label: 'Stopped safely', className: 'is-attention', symbol: '⏹' },
};

const STAGE_STATE_LABEL: Record<GuidedStage['state'], string> = {
  planned: 'Planned',
  active: 'In progress',
  completed: 'Completed',
  attention: 'Needs attention',
  skipped: 'Not needed',
};

const STAGE_ACTIVITY: Record<string, string> = {
  prepare: 'Preparing',
  understand: 'Understanding',
  gather: 'Gathering',
  create: 'Creating',
  check: 'Checking',
  finalise: 'Finalising',
};

function useElapsed(startedAt: number | null | undefined, endedAt: number | null | undefined): string {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (startedAt == null || endedAt != null) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [startedAt, endedAt]);
  if (startedAt == null) return 'Not started';
  const seconds = Math.max(0, (endedAt ?? now) - startedAt);
  if (seconds < 60) return `${Math.floor(seconds)} sec`;
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60);
  return `${minutes} min ${remaining.toString().padStart(2, '0')} sec`;
}

function stepStatusSentence(step: GuidedStep, stage: GuidedStage | null): string {
  if (step.status === 'paused') return `A decision is needed before ${step.displayName.toLowerCase()} can continue.`;
  if (step.status === 'failed') return step.failureMessage || `${step.displayName} could not finish as intended. Completed work remains available.`;
  if (step.status === 'done' || step.status === 'reused') return `${step.displayName} finished and its contribution is available.`;
  if (step.status === 'pending') return `${step.displayName} is planned in the ${stage?.displayName ?? 'current'} stage.`;
  const activity = STAGE_ACTIVITY[stage?.id ?? ''] ?? 'Working on';
  return `${activity} ${step.displayName.toLowerCase()}.`;
}

function activityText(event: RunEvent, stepById: Map<string, GuidedStep>): string {
  const nodeId = 'node_id' in event ? event.node_id : undefined;
  const stepName = nodeId ? stepById.get(nodeId)?.displayName : null;
  if (event.type === 'node_started') return `${stepName ?? 'A workflow step'} started.`;
  if (event.type === 'node_completed') return `${stepName ?? 'A workflow step'} completed.`;
  if (event.type === 'node_reused') return `${stepName ?? 'A workflow step'} reused previously completed work.`;
  if (event.type === 'node_paused') return `${stepName ?? 'The workflow'} is waiting for your review.`;
  if (event.type === 'run_completed') return 'The workflow completed and its outputs are ready.';
  if (event.type === 'run_rejected') return 'The workflow stopped after a review decision.';
  return 'The workflow needs attention before it can continue.';
}

function GuidedStageJourney({
  stages,
  selectedStageId,
  onSelect,
}: {
  stages: GuidedStage[];
  selectedStageId: string | null;
  onSelect: (stageId: string) => void;
}) {
  return (
    <nav className="guided-journey" aria-label="Workflow stages">
      {stages.map((stage, index) => (
        <button
          key={stage.id}
          type="button"
          className={`guided-stage is-${stage.state} ${selectedStageId === stage.id ? 'is-selected' : ''}`}
          onClick={() => onSelect(stage.id)}
          aria-current={stage.state === 'active' ? 'step' : undefined}
        >
          <span className="guided-stage-index" aria-hidden="true">
            {stage.state === 'completed' ? '✓' : index + 1}
          </span>
          <span className="guided-stage-copy">
            <strong>{stage.displayName}</strong>
            <span>{STAGE_STATE_LABEL[stage.state]}</span>
          </span>
        </button>
      ))}
    </nav>
  );
}

function ContributionCard({ step }: { step: GuidedStep }) {
  return (
    <article className="guided-contribution-card">
      <div className="guided-card-heading">
        <div>
          <div className="guided-eyebrow">Contribution ready</div>
          <h3>{step.displayName}</h3>
        </div>
        <span className="guided-state-chip is-complete"><span aria-hidden="true">✓</span> Completed</span>
      </div>
      <p className="guided-outcome">{step.outcome}</p>
      {step.keyPoints.length > 0 && (
        <ul className="guided-key-points">
          {step.keyPoints.map((point, index) => <li key={index}>{point}</li>)}
        </ul>
      )}
      <div className="guided-contribution-meta">
        <div>
          <span>Next use</span>
          <p>{step.receivingSteps.length > 0
            ? step.receivingSteps.join(', ')
            : step.contribution}</p>
        </div>
        <div>
          <span>Quality and gaps</span>
          <p>{step.qualitySummary}</p>
        </div>
      </div>
      {step.output != null && (
        <details className="guided-details">
          <summary>View result details</summary>
          <pre>{typeof step.output === 'string' ? step.output : JSON.stringify(step.output, null, 2)}</pre>
        </details>
      )}
    </article>
  );
}

export function GuidedRun() {
  const run = useCockpitRun();
  const {
    runId,
    navState,
    parsedWf,
    navigate,
    triggerError,
    liveRun,
    gate,
    setGateHidden,
    gateFetchError,
    retryGateFetch,
    finished,
    events,
    streamError,
    cockpit,
    activeNodeId,
    applyResumeResult,
    setTriggerError,
    pipelineDoc,
    continueToNextStage,
    continuingStage,
    continueError,
  } = run;
  const [emphasis, setEmphasis] = useState<Emphasis>(() => (
    window.localStorage.getItem('eurskem.guided.emphasis') === 'expert' ? 'expert' : 'administrator'
  ));
  const [section, setSection] = useState<GuidedSection>('overview');
  const [selectedStageId, setSelectedStageId] = useState<string | null | undefined>(undefined);
  const [reviewGateId, setReviewGateId] = useState<string | null>(null);
  const [fullOutputOpen, setFullOutputOpen] = useState(false);
  const [retryError, setRetryError] = useState<string | null>(null);

  useEffect(() => {
    window.localStorage.setItem('eurskem.guided.emphasis', emphasis);
  }, [emphasis]);

  const finishedState = finished?.state as Record<string, unknown> | undefined;
  const finishedOutputs = finishedState?.node_outputs;
  const outputs = useMemo(() => ({
    ...(liveRun?.outputs ?? {}),
    ...(finishedOutputs && typeof finishedOutputs === 'object' && !Array.isArray(finishedOutputs)
      ? finishedOutputs as Record<string, unknown>
      : {}),
  }), [finishedOutputs, liveRun?.outputs]);
  const terminal = Boolean(finished) || ['completed', 'failed', 'rejected'].includes(liveRun?.status ?? '');
  const statuses = useMemo(() => parsedWf ? nodeStatusesFromRun(
    parsedWf,
    cockpit.nodeStates,
    liveRun,
    terminal,
  ) : {}, [cockpit.nodeStates, liveRun, parsedWf, terminal]);
  const model = useMemo(() => parsedWf ? buildGuidedRuntimeModel({
    workflow: parsedWf,
    nodeStatuses: statuses,
    outputs,
    activeNodeId,
    gateNodeId: gate?.nodeId,
  }) : null, [activeNodeId, gate?.nodeId, outputs, parsedWf, statuses]);
  const artifacts = useMemo(() => collectGuidedArtifacts(outputs), [outputs]);
  const elapsed = useElapsed(liveRun?.started_at, liveRun?.ended_at);

  if (!runId) return <div className="guided-empty">No run was selected.</div>;
  if (!parsedWf || !model) {
    return (
      <div className="guided-loading" role="status">
        <span className="guided-loading-mark" aria-hidden="true" />
        <div>
          <h2>Opening your guided workspace</h2>
          <p>Loading the saved workflow and latest progress for this run.</p>
        </div>
        {triggerError && <p className="guided-error-copy">{triggerError}</p>}
      </div>
    );
  }

  const displayStatus = finished?.status
    ?? (gate ? 'paused' : liveRun?.status)
    ?? cockpit.runStatus
    ?? 'connecting';
  const statusMeta = RUN_STATUS[displayStatus] ?? RUN_STATUS.connecting;
  const currentStage = model.currentStage;
  const currentStep = model.currentStep;
  const effectiveSelectedStageId = selectedStageId === undefined
    ? model.currentStage?.id ?? null
    : selectedStageId;
  const selectedContributions = model.contributions
    .filter(step => !effectiveSelectedStageId || step.stageId === effectiveSelectedStageId)
    .slice()
    .reverse();
  const failedSteps = model.steps.filter(step => step.status === 'failed');
  const reviewSteps = model.contributions.filter(step => (
    /blocking|review is required|review item/i.test(step.qualitySummary)
  ));
  const attentionCount = (gate ? 1 : 0) + failedSteps.length + reviewSteps.length;
  const stepById = new Map(model.steps.map(step => [step.id, step]));
  const outputState = finished?.state ?? {
    node_outputs: outputs,
    inputs: liveRun?.inputs ?? navState.inputs ?? {},
    variables: liveRun?.variables ?? {},
  };
  const pausedStep = gate ? stepById.get(gate.nodeId) : null;
  const runWorkflowYaml = navState.workflowYaml ?? liveRun?.workflow_yaml;
  const workflowName = navState.workflowName ?? liveRun?.workflow_name ?? parsedWf.name;
  const pipelineHasNext = Boolean(
    pipelineDoc
    && pipelineDoc.current_stage_index + 1 < pipelineDoc.stages.length
    && finished?.status === 'completed',
  );

  function openProcessMap() {
    navigate(`/cockpit/${runId}`, {
      state: {
        attach: true,
        workflowYaml: runWorkflowYaml,
        workflowName,
        selectedNodeId: currentStep?.id ?? activeNodeId ?? undefined,
      },
    });
  }

  function retryRun() {
    if (!liveRun) return;
    const error = startRetryRun(liveRun, navigate, 'guided');
    setRetryError(error);
  }

  const headerPrimaryAction = gate ? (
    <button className="ui-button ui-button--primary" onClick={() => setReviewGateId(gate.nodeId)}>
      Review now
    </button>
  ) : displayStatus === 'completed' ? (
    <button className="ui-button ui-button--primary" onClick={() => setSection('outputs')}>
      Review outputs
    </button>
  ) : displayStatus === 'failed' ? (
    <button className="ui-button ui-button--primary" onClick={retryRun} disabled={!liveRun?.retry_available}>
      Retry safely
    </button>
  ) : (
    <button className="ui-button ui-button--secondary" onClick={openProcessMap}>
      View process map
    </button>
  );

  return (
    <div className={`guided-run guided-run--${emphasis}`}>
      <div className="guided-live-region" role="status" aria-live="polite" aria-atomic="true">
        {statusMeta.label}. {currentStep ? stepStatusSentence(currentStep, currentStage) : ''}
      </div>

      <header className="guided-run-header">
        <div className="guided-run-header-main">
          <div className="guided-run-title-row">
            <span className={`guided-run-status ${statusMeta.className}`}>
              <span aria-hidden="true">{statusMeta.symbol}</span> {statusMeta.label}
            </span>
            {attentionCount > 0 && (
              <span className="guided-attention-count">{attentionCount} item{attentionCount === 1 ? '' : 's'} need review</span>
            )}
          </div>
          <h1>{workflowName}</h1>
          <p className="guided-run-goal">{model.goal}</p>
          <div className="guided-run-facts">
            <span><strong>{model.completedStageCount}</strong> of {model.stages.length} stages complete</span>
            <span><strong>{elapsed}</strong> elapsed</span>
            {currentStage && <span>Current stage: <strong>{currentStage.displayName}</strong></span>}
          </div>
        </div>
        <div className="guided-run-header-actions">
          <label className="guided-emphasis-picker">
            <span>View</span>
            <select value={emphasis} onChange={event => setEmphasis(event.target.value as Emphasis)}>
              <option value="administrator">Project Administrator</option>
              <option value="expert">Domain Expert</option>
            </select>
          </label>
          {headerPrimaryAction}
        </div>
      </header>

      <GuidedStageJourney
        stages={model.stages}
        selectedStageId={effectiveSelectedStageId}
        onSelect={setSelectedStageId}
      />

      <div className="guided-section-tabs" role="tablist" aria-label="Guided run sections">
        {([
          ['overview', 'Overview'],
          ['outputs', `Outputs${artifacts.length ? ` (${artifacts.length})` : ''}`],
          ['activity', 'Activity'],
        ] as Array<[GuidedSection, string]>).map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={section === key}
            className={section === key ? 'is-active' : ''}
            onClick={() => setSection(key)}
          >
            {label}
          </button>
        ))}
        <button type="button" className="guided-process-link" onClick={openProcessMap}>
          Process map
        </button>
      </div>

      {section === 'overview' && (
        <div className="guided-runtime-grid">
          <main className="guided-main-column">
            <section className="guided-current-work" aria-labelledby="guided-current-heading">
              <div className="guided-card-heading">
                <div>
                  <div className="guided-eyebrow">Current work</div>
                  <h2 id="guided-current-heading">
                    {currentStep?.displayName ?? (displayStatus === 'completed' ? 'All requested work is complete' : 'Preparing the next activity')}
                  </h2>
                </div>
                {currentStep && (
                  <span className={`guided-state-chip is-${currentStep.status}`}>
                    <span aria-hidden="true">{currentStep.status === 'done' ? '✓' : currentStep.status === 'paused' ? '!' : '●'}</span>
                    {GUIDED_STATUS_LABEL[currentStep.status]}
                  </span>
                )}
              </div>
              {currentStep ? (
                <>
                  <p className="guided-current-status">{stepStatusSentence(currentStep, currentStage)}</p>
                  <div className="guided-current-explanation">
                    <div>
                      <span>Why this matters</span>
                      <p>{currentStep.purpose}</p>
                    </div>
                    <div>
                      <span>Expected handoff</span>
                      <p>{currentStep.contribution}</p>
                    </div>
                  </div>
                  {currentStep.showRole && currentStep.role && (
                    <div className="guided-role-note">Responsible role: {currentStep.role}</div>
                  )}
                  {currentStage && currentStage.totalCount > 1 && (
                    <div className="guided-stage-milestone">
                      <div>
                        <span>{currentStage.displayName} milestone</span>
                        <strong>{currentStage.completedCount} of {currentStage.totalCount} activities finished</strong>
                      </div>
                      <div className="guided-milestone-track" aria-hidden="true">
                        <span style={{ width: `${Math.round((currentStage.completedCount / currentStage.totalCount) * 100)}%` }} />
                      </div>
                    </div>
                  )}
                </>
              ) : (
                <p className="guided-current-status">The run is preparing its first meaningful activity.</p>
              )}
            </section>

            <section className="guided-contributions" aria-labelledby="guided-contributions-heading">
              <div className="guided-section-heading">
                <div>
                  <div className="guided-eyebrow">Completed work</div>
                  <h2 id="guided-contributions-heading">Contributions to the final result</h2>
                </div>
                {effectiveSelectedStageId && (
                  <button type="button" onClick={() => setSelectedStageId(null)}>Show all stages</button>
                )}
              </div>
              {selectedContributions.length === 0 ? (
                <div className="guided-empty-card">
                  <strong>No completed contributions in this stage yet.</strong>
                  <span>Results will appear here as meaningful work finishes.</span>
                </div>
              ) : selectedContributions.map(step => <ContributionCard key={step.id} step={step} />)}
            </section>
          </main>

          <aside className="guided-side-column">
            <section className={`guided-attention-panel ${attentionCount > 0 ? 'has-attention' : ''}`} aria-labelledby="guided-attention-heading">
              <div className="guided-card-heading">
                <div>
                  <div className="guided-eyebrow">Attention</div>
                  <h2 id="guided-attention-heading">What needs you</h2>
                </div>
                <span className="guided-attention-badge">{attentionCount}</span>
              </div>
              {gate && (
                <article className="guided-decision-card">
                  <span>Decision required</span>
                  <h3>{pausedStep?.displayName ?? 'Review the current result'}</h3>
                  <p>{gate.question || 'Confirm this work before dependent activities continue.'}</p>
                  <p className="guided-impact-copy">
                    Why now: {pausedStep?.contribution ?? 'Your decision controls what dependent work uses next.'}
                  </p>
                  <button className="ui-button ui-button--primary" onClick={() => setReviewGateId(gate.nodeId)}>
                    Review and respond
                  </button>
                </article>
              )}
              {failedSteps.map(step => (
                <article className="guided-recovery-card" key={step.id}>
                  <span>Work needs attention</span>
                  <h3>{step.displayName}</h3>
                  <p>{step.failureMessage || 'This activity could not finish. Completed work remains safe.'}</p>
                  <p>{suggestedCorrectiveAction(liveRun?.node_runs?.[step.id]?.error) || 'Open the process map for details or retry from the last safe checkpoint.'}</p>
                </article>
              ))}
              {reviewSteps.slice(0, 3).map(step => (
                <article className="guided-review-item" key={step.id}>
                  <strong>{step.displayName}</strong>
                  <span>{step.qualitySummary}</span>
                </article>
              ))}
              {attentionCount === 0 && (
                <div className="guided-no-attention">
                  <span aria-hidden="true">✓</span>
                  <div><strong>No action needed</strong><p>Independent work will continue automatically.</p></div>
                </div>
              )}
              {gateFetchError && (
                <div className="guided-inline-error">
                  <p>We could not load the saved review question.</p>
                  <button className="ui-button ui-button--secondary" onClick={retryGateFetch}>Try again</button>
                </div>
              )}
            </section>

            <section className="guided-output-panel" aria-labelledby="guided-output-heading">
              <div className="guided-card-heading">
                <div>
                  <div className="guided-eyebrow">Latest result</div>
                  <h2 id="guided-output-heading">Outputs</h2>
                </div>
                <button type="button" onClick={() => setSection('outputs')}>View all</button>
              </div>
              {artifacts.slice(0, 4).map(artifact => (
                <div className="guided-artifact-row" key={artifact.key}>
                  <span className="guided-file-type">{artifact.extension.toUpperCase()}</span>
                  <div><strong>{artifact.label}</strong><span>Ready to open or download</span></div>
                  <button type="button" onClick={() => api.downloadArtifact(artifact.key).catch(error => setTriggerError(String(error)))}>
                    Download
                  </button>
                </div>
              ))}
              {artifacts.length === 0 && model.contributions.length > 0 && (
                <div className="guided-latest-summary">
                  <strong>{model.contributions.at(-1)?.displayName}</strong>
                  <p>{model.contributions.at(-1)?.outcome}</p>
                </div>
              )}
              {artifacts.length === 0 && model.contributions.length === 0 && (
                <p className="guided-muted-copy">Deliverables will remain available here as they are produced.</p>
              )}
            </section>

            <details className="guided-advanced-details">
              <summary>Advanced details</summary>
              <div>
                <span>Run reference</span><code>{runId}</code>
                {streamError && <p>Live updates are reconnecting. The run continues in the background.</p>}
                {triggerError && <p className="guided-error-copy">{triggerError}</p>}
                <button className="ui-button ui-button--secondary" onClick={openProcessMap}>Open technical process map</button>
              </div>
            </details>
          </aside>
        </div>
      )}

      {section === 'outputs' && (
        <section className="guided-full-section" aria-labelledby="guided-all-outputs">
          <div className="guided-section-heading">
            <div><div className="guided-eyebrow">Persistent deliverables</div><h2 id="guided-all-outputs">Outputs and versions</h2></div>
            <button className="ui-button ui-button--secondary" onClick={() => setFullOutputOpen(true)}>Open detailed output workspace</button>
          </div>
          {artifacts.length > 0 ? (
            <div className="guided-artifact-grid">
              {artifacts.map(artifact => (
                <article key={artifact.key}>
                  <span className="guided-file-type">{artifact.extension.toUpperCase()}</span>
                  <h3>{artifact.label}</h3>
                  <p>Produced by {stepById.get(artifact.nodeId)?.displayName ?? 'the workflow'}.</p>
                  <button className="ui-button ui-button--primary" onClick={() => api.downloadArtifact(artifact.key).catch(error => setTriggerError(String(error)))}>
                    Download
                  </button>
                </article>
              ))}
            </div>
          ) : (
            <div className="guided-empty-card"><strong>No downloadable files yet.</strong><span>Structured contributions are available in Overview while the workflow continues.</span></div>
          )}
          {pipelineHasNext && (
            <div className="guided-next-stage-callout">
              <div><strong>This pipeline stage is complete.</strong><span>The next business stage is ready to start.</span></div>
              <button className="ui-button ui-button--primary" onClick={continueToNextStage} disabled={continuingStage}>
                {continuingStage ? 'Starting next stage…' : 'Continue to next stage'}
              </button>
              {continueError && <p className="guided-error-copy">{continueError}</p>}
            </div>
          )}
        </section>
      )}

      {section === 'activity' && (
        <section className="guided-full-section" aria-labelledby="guided-activity-heading">
          <div className="guided-section-heading">
            <div><div className="guided-eyebrow">Human-readable history</div><h2 id="guided-activity-heading">Meaningful activity</h2></div>
            <button className="ui-button ui-button--secondary" onClick={openProcessMap}>Open technical diagnostics</button>
          </div>
          <ol className="guided-activity-list">
            {events.length === 0 && <li><span className="guided-activity-dot" /><div><strong>Preparing live updates</strong><p>The run will continue even if this page is closed.</p></div></li>}
            {events.slice().reverse().map((event, index) => (
              <li key={event.event_id ?? `${event.type}-${index}`}>
                <span className="guided-activity-dot" />
                <div><strong>{activityText(event, stepById)}</strong><p>{new Date(event.ts).toLocaleTimeString()}</p></div>
              </li>
            ))}
          </ol>
        </section>
      )}

      {(retryError || triggerError) && (
        <div className="guided-toast" role="alert">{retryError ?? triggerError}</div>
      )}

      {gate && reviewGateId === gate.nodeId && (
        <div className="guided-modal" role="dialog" aria-modal="true" aria-label="Review required">
          <div className="guided-modal-card guided-modal-card--review">
            <HITLPanel
              key={`${runId}:${gate.nodeId}`}
              runId={runId}
              pausedNodeId={gate.nodeId}
              pausedStepName={pausedStep?.displayName}
              reviewPurpose={pausedStep?.purpose}
              downstreamSummary={pausedStep?.contribution}
              question={gate.question}
              context={gate.context}
              allowedActions={gate.allowedActions}
              content={gate.content}
              allowDocumentOverride={gate.allowDocumentOverride}
              maxEditChars={gate.maxEditChars}
              onResult={applyResumeResult}
              onSubmitting={() => { setGateHidden(true); setReviewGateId(null); }}
              onSubmitError={message => setTriggerError(message)}
              onClose={() => setReviewGateId(null)}
            />
          </div>
        </div>
      )}

      {fullOutputOpen && (
        <div className="guided-modal" role="dialog" aria-modal="true" aria-label="Detailed output workspace">
          <div className="guided-modal-card guided-modal-card--output">
            <div className="guided-modal-toolbar">
              <div><strong>Detailed output workspace</strong><span>Sources, variables, audit and scoring remain available here.</span></div>
              <button className="ui-button ui-button--secondary" onClick={() => setFullOutputOpen(false)}>Close</button>
            </div>
            <div className="guided-modal-output-body">
              <OutputViewer runId={runId} state={outputState} workflowName={workflowName} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
