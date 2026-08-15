import { useCallback, useMemo, useState, type FormEvent } from 'react';

import { api } from '../../api/client';
import type { BusinessAction, BusinessExplanation, BusinessFact } from '../../api/types';
import { HITLPanel } from './HITLPanel';
import { AskAiPanel } from './run-history/AskAiPanel';
import { useCockpitRun } from './cockpit/useCockpitRun';
import { startRetryRun } from './cockpit/node-render';
import { ActionOutcomeDialog, ActionPromptDialog, TechnicalDrawer } from './business/dialogs';
import { Card, Modal } from './business/primitives';
import {
  ActionCenter,
  ActivityList,
  AttentionCenter,
  BusinessTimeline,
  ContextSidebar,
  DecisionCard,
  NextStepCard,
  StatusHero,
  StatusSummary,
  UnderstandingCard,
} from './business/panels';
import { useBusinessActions } from './business/useBusinessActions';
import { useBusinessNarration, useBusinessProjection } from './business/useBusinessProjection';

/**
 * The Business View: where a business employee works with an AI-assisted
 * process — not a nicer visualisation of workflow execution.
 *
 * Everything on screen is decided by the server's business projection: what
 * the activities are, which facts matter, where each came from, what needs
 * attention and which actions this person may take right now. This component
 * lays that out in priority order (§40) and wires the typed actions; it never
 * re-derives meaning from node names or event types, and it never renders raw
 * model output — that lives one level deeper, behind "Technical details".
 */
export function BusinessView() {
  const run = useCockpitRun();
  const {
    runId, navState, navigate, triggerError, liveRun, gate, setGateHidden,
    gateFetchError, retryGateFetch, finished, events, streamError,
    applyResumeResult, setTriggerError, pipelineDoc, continueToNextStage,
    continuingStage, continueError,
  } = run;

  const [reviewOpen, setReviewOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [askSeed, setAskSeed] = useState<string | null>(null);
  const [technicalActivity, setTechnicalActivity] = useState<string | null>(null);
  const [explanation, setExplanation] = useState<BusinessExplanation | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [controlError, setControlError] = useState<string | null>(null);
  const [commandInput, setCommandInput] = useState('');

  const { projection, error: projectionError, loading, throttled, refetch } =
    useBusinessProjection(runId, events, gate?.nodeId, finished?.status);
  const narration = useBusinessNarration(runId, projection?.business_status.state_version);

  const openAsk = useCallback((question?: string) => {
    setAskSeed(question ?? null);
    setAskOpen(true);
  }, []);

  const handleStop = useCallback(async () => {
    if (!runId) return;
    // `deleteRun` is the only stop primitive the platform has, so this really
    // does destroy the work item. Say so plainly rather than dressing it up.
    if (!window.confirm('This permanently stops and deletes this work item and its history. Continue?')) return;
    try {
      await api.deleteRun(runId);
      navigate('/history');
    } catch (e) {
      setControlError(e instanceof Error ? e.message : String(e));
    }
  }, [runId, navigate]);

  const handleResume = useCallback(async () => {
    if (!runId) return;
    try {
      applyResumeResult(await api.resumePausedRun(runId));
    } catch (e) {
      setControlError(e instanceof Error ? e.message : String(e));
    }
  }, [runId, applyResumeResult]);

  const handleRerun = useCallback((mode: string) => {
    if (!liveRun) return;
    if (mode === 'retry') {
      setControlError(startRetryRun(liveRun, navigate, 'business'));
      return;
    }
    // A restart re-runs the same inputs as a new work item, so the original
    // stays intact as the record of what was decided the first time.
    const nextRunId = crypto.randomUUID();
    api.restartRun(liveRun.run_id, nextRunId)
      .then(() => navigate(`/business/${nextRunId}`, { state: { attach: true, workflowName: liveRun.workflow_name } }))
      .catch(e => setControlError(e instanceof Error ? e.message : String(e)));
  }, [liveRun, navigate]);

  const actions = useBusinessActions(runId, useMemo(() => ({
    onReview: () => setReviewOpen(true),
    onTechnical: (activityId: string) => setTechnicalActivity(activityId),
    onAsk: openAsk,
    onOpenAttachment: (fileKey: string) => {
      api.downloadArtifact(fileKey).catch(e => setControlError(String(e)));
    },
    onRerun: handleRerun,
    onStop: handleStop,
    onResume: handleResume,
    onChanged: refetch,
  }), [openAsk, handleRerun, handleStop, handleResume, refetch]));

  const loadExplanation = useCallback(async () => {
    if (!runId || explanation) return;
    setExplaining(true);
    try {
      setExplanation(await api.businessExplanation(runId));
    } catch {
      // The decision card already shows the deterministic facts and rules it
      // was given; a failed rewrite is not worth an error banner.
    } finally {
      setExplaining(false);
    }
  }, [runId, explanation]);

  const openCockpit = useCallback(() => {
    navigate(`/cockpit/${runId}`, {
      state: {
        attach: true,
        workflowYaml: navState.workflowYaml ?? liveRun?.workflow_yaml,
        workflowName: navState.workflowName ?? liveRun?.workflow_name,
      },
    });
  }, [navigate, runId, navState, liveRun]);

  const onEditFact = useCallback((action: BusinessAction, fact: BusinessFact) => {
    actions.editFact(action, String(action.params.field ?? fact.id), fact.label, fact.value);
  }, [actions]);

  if (!runId) return <div className="p-8 text-ink-500">No run was selected.</div>;

  if (loading) {
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

  if (!projection) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 p-16 text-center" role="status">
        <h2 className="text-lg font-semibold text-ink-900">This work item could not be opened</h2>
        <p className="text-sm text-bad">{projectionError ?? triggerError}</p>
      </div>
    );
  }

  const status = narration
    ? { ...projection.business_status, headline: narration.headline, summary: narration.summary,
        narration_source: narration.source, narration_model: narration.model ?? null }
    : projection.business_status;
  const shown = { ...projection, business_status: status };
  const approvalAction = projection.required_user_actions.find(item => item.type === 'approval_review');
  const heroActions = projection.allowed_actions.filter(action =>
    ['approve', 'resume_run', 'pause_run', 'assign_work_item', 'stop_run', 'open_technical_details'].includes(action.type),
  ).slice(0, 5);

  function submitCommand(event: FormEvent) {
    event.preventDefault();
    const trimmed = commandInput.trim();
    if (!trimmed) return;
    openAsk(trimmed);
    setCommandInput('');
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-slate-50">
      <StatusSummary status={status} />

      <StatusHero
        projection={shown}
        actions={heroActions}
        onRun={actions.run}
        busyId={actions.busyId}
        narrated={status.narration_source === 'ai'}
      />

      {(controlError || actions.error) && (
        <p className="bg-bad/5 px-6 py-2 text-sm text-bad">{controlError ?? actions.error}</p>
      )}
      {throttled && (
        <p className="bg-warn/5 px-6 py-2 text-sm text-warn">
          Updates are arriving faster than they can be loaded. Showing the last known state; it will catch up shortly.
        </p>
      )}

      {/* Priority order (§40): attention, decision, next step, then detail. */}
      <div className="grid flex-1 grid-cols-1 gap-4 p-4 lg:grid-cols-[minmax(0,1fr)_340px]">
        <main className="flex min-w-0 flex-col gap-4">
          {approvalAction && (
            <Card title="Approval required" tone="attention">
              <p className="text-sm text-ink-900">
                {approvalAction.question || 'Confirm this work before it continues.'}
              </p>
              <button
                type="button"
                onClick={() => setReviewOpen(true)}
                className="mt-3 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700"
              >
                Review and respond
              </button>
            </Card>
          )}

          <AttentionCenter items={projection.attention} onRun={actions.run} busyId={actions.busyId} />

          {projection.decision && (
            <DecisionCard
              decision={projection.decision}
              onRun={actions.run}
              busyId={actions.busyId}
              explanation={explanation}
              explaining={explaining}
              onExplain={loadExplanation}
            />
          )}

          {projection.next_step && (
            <NextStepCard nextStep={projection.next_step} onRun={actions.run} busyId={actions.busyId} />
          )}

          <UnderstandingCard
            understanding={projection.understanding}
            onRun={actions.run}
            onEditFact={onEditFact}
            busyId={actions.busyId}
          />

          <ActionCenter
            recommended={projection.recommended_actions}
            other={projection.other_actions}
            onRun={actions.run}
            busyId={actions.busyId}
          />

          <ActivityList
            activities={projection.activities}
            summary={projection.activity_summary}
            onRun={actions.run}
            busyId={actions.busyId}
            onTechnical={setTechnicalActivity}
          />

          <Card title="History">
            <BusinessTimeline entries={projection.timeline} />
          </Card>

          {streamError && (
            <p className="text-sm text-ink-400">
              Live updates are reconnecting. This work continues in the background.
            </p>
          )}
          {gateFetchError && (
            <Card title="Review" tone="attention">
              <p className="text-sm text-bad">We could not load the saved review question.</p>
              <button type="button" onClick={retryGateFetch} className="mt-1 text-sm font-medium text-accent-700 hover:underline">
                Try again
              </button>
            </Card>
          )}
        </main>

        {/* Collapses under the main column on a narrow screen (§69). */}
        <aside className="min-w-0">
          <ContextSidebar projection={shown} onRun={actions.run} busyId={actions.busyId} />
          {pipelineDoc && pipelineDoc.current_stage_index + 1 < pipelineDoc.stages.length
            && finished?.status === 'completed' && (
            <div className="mt-4">
              <Card title="Next stage" tone="primary">
                <p className="text-sm text-ink-700">The next stage in this process is ready to start.</p>
                <button
                  type="button"
                  onClick={continueToNextStage}
                  disabled={continuingStage}
                  className="mt-2 rounded-md bg-accent-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50"
                >
                  {continuingStage ? 'Starting next stage…' : 'Continue to next stage'}
                </button>
                {continueError && <p className="mt-1 text-sm text-bad">{continueError}</p>}
              </Card>
            </div>
          )}
        </aside>
      </div>

      {/* Suggested prompts change with the work item's state (§31). */}
      <footer className="flex-none border-t border-slate-200 bg-white px-6 py-3">
        {projection.suggested_questions.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-2">
            {projection.suggested_questions.map(question => (
              <button
                key={question}
                type="button"
                onClick={() => openAsk(question)}
                className="rounded-full border border-slate-300 px-3 py-1 text-xs font-medium text-ink-700 hover:bg-slate-50"
              >
                {question}
              </button>
            ))}
          </div>
        )}
        <form onSubmit={submitCommand} className="flex items-center gap-2">
          <input
            value={commandInput}
            onChange={event => setCommandInput(event.target.value)}
            placeholder="Ask about this work item…"
            aria-label="Ask about this work item"
            className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-400"
          />
          <button
            type="submit"
            disabled={!commandInput.trim()}
            className="flex-none rounded-md bg-accent-600 px-3 py-2 text-sm font-medium text-white hover:bg-accent-700 disabled:opacity-50"
          >
            Ask
          </button>
        </form>
      </footer>

      {actions.prompt && (
        <ActionPromptDialog
          prompt={actions.prompt}
          busy={actions.busyId != null}
          error={actions.error}
          onSubmit={actions.submitPrompt}
          onClose={() => { actions.setPrompt(null); actions.clearError(); }}
        />
      )}

      {actions.outcome && (
        <ActionOutcomeDialog outcome={actions.outcome} onClose={actions.clearOutcome} />
      )}

      {technicalActivity && (
        <TechnicalDrawer
          runId={runId}
          activityId={technicalActivity}
          onClose={() => setTechnicalActivity(null)}
          onOpenCockpit={openCockpit}
        />
      )}

      {gate && reviewOpen && (
        <Modal title="Review required" onClose={() => setReviewOpen(false)} wide>
          <HITLPanel
            key={`${runId}:${gate.nodeId}`}
            runId={runId}
            pausedNodeId={gate.nodeId}
            pausedStepName={projection.business_status.headline}
            reviewPurpose={approvalAction?.question ?? undefined}
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
        </Modal>
      )}

      {askOpen && (
        <Modal
          title="Ask about this work item"
          description="Answers only what's recorded on this run."
          onClose={() => { setAskOpen(false); setAskSeed(null); }}
        >
          <AskAiPanel runId={runId} initialQuestion={askSeed ?? undefined} />
        </Modal>
      )}
    </div>
  );
}
