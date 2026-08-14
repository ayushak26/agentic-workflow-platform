// Extracted, behavior-preserving, from the original RunHistory.tsx: owns
// the run-list poll, the selected-run detail poll, and every action
// (pause/resume/restart/delete/retry). Same shape as cockpit/useCockpitRun.ts
// — separating data-fetching/actions from presentation.
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api, rehydrate } from '../../../api/client';
import { startRetryRun } from '../cockpit/node-render';
import type { AuditEvent, RunDetail, RunSummary } from '../../../api/types';

// Keep in sync with settings.run_delete_min_running_age_seconds
// (app/config.py) — the backend is the actual source of truth and rejects
// the delete regardless, this only avoids offering a button that's certain
// to 409.
const DELETE_MIN_RUNNING_AGE_SECONDS = 24 * 60 * 60;

export function deleteBlockedReason(run: RunSummary): string | null {
  if (run.status !== 'running') return null;
  if (run.started_at == null) {
    return "Still running — can't tell how long yet.";
  }
  const ageSeconds = Date.now() / 1000 - run.started_at;
  const remaining = DELETE_MIN_RUNNING_AGE_SECONDS - ageSeconds;
  if (remaining <= 0) return null;
  const hoursLeft = Math.max(1, Math.ceil(remaining / 3600));
  return (
    `Still running — deletable once it's been running 24h `
    + `(about ${hoursLeft}h left).`
  );
}

const TERMINAL_STATUSES = new Set(['completed', 'rejected', 'failed']);

// A higher fixed cap, not cursor pagination (documented limitation) — the
// left panel virtualizes rendering, so a larger single fetch is cheap
// server-side (indexed sort+limit) and simpler than building real paging.
const RUN_LIST_LIMIT = 200;

export function useRunHistoryData(runId: string | undefined) {
  const navigate = useNavigate();

  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [listErr, setListErr] = useState<string | null>(null);
  const [detail, setDetail] = useState<{ run: RunDetail; audit: AuditEvent[] } | null>(null);
  const [detailErr, setDetailErr] = useState<string | null>(null);
  const [retryErr, setRetryErr] = useState<string | null>(null);
  const [actionErr, setActionErr] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState<'pause' | 'resume' | 'restart' | 'delete' | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [autofixBusy, setAutofixBusy] = useState(false);
  const [autofixErr, setAutofixErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const load = () => {
      api.runHistory(RUN_LIST_LIMIT)
        .then((data) => {
          if (cancelled) return;
          // Replacing the whole array on every poll is fine — selection is
          // tracked by run_id (a stable id) in the URL, not by array index
          // or object identity, so a new reference here never reorders or
          // loses the run the user is currently looking at.
          setRuns(data.runs);
          setListErr(null);
        })
        .catch(async (error) => {
          if (cancelled) return;
          const msg = String(error);
          // On auth failure, stop polling and try to recover the session from
          // the cookie once. If that fails, surface it instead of hammering.
          if (msg.includes('401')) {
            if (timer) window.clearInterval(timer);
            const user = await rehydrate();
            if (!cancelled && user) {
              load(); // session recovered — resume
              timer = window.setInterval(load, 2500);
            } else if (!cancelled) {
              setListErr('Session expired — please log in again.');
            }
            return;
          }
          setListErr(msg);
        });
    };

    load();
    timer = window.setInterval(load, 2500);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [refreshToken]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    // Clear the previous route's detail before synchronizing the new run.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setDetail(null);
    setDetailErr(null);
    setRetryErr(null);
    setActionErr(null);
    let timer: number | undefined;
    const load = () => {
      api.runDetail(runId)
        .then((data) => {
          if (cancelled) return;
          setDetail(data);
          setDetailErr(null);
          // Nothing left to change once the run has ended — stop polling
          // instead of re-fetching an identical payload every 2s forever.
          if (TERMINAL_STATUSES.has(data.run.status) && timer) {
            window.clearInterval(timer);
            timer = undefined;
          }
        })
        .catch((error) => {
          if (!cancelled) setDetailErr(String(error));
        });
    };
    load();
    timer = window.setInterval(load, 2000);
    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
    };
  }, [runId]);

  function refresh() {
    setRefreshToken((v) => v + 1);
  }

  function retryFailedRun() {
    if (!detail) return;
    const error = startRetryRun(detail.run, navigate);
    if (error) setRetryErr(error);
  }

  function openInCockpit(selectedNodeId?: string | null) {
    if (!detail?.run.workflow_yaml) return;
    navigate(`/cockpit/${detail.run.run_id}`, {
      state: {
        attach: true,
        workflowYaml: detail.run.workflow_yaml,
        workflowName: detail.run.workflow_name,
        selectedNodeId: selectedNodeId ?? undefined,
      },
    });
  }

  function openInBusinessView() {
    if (!detail?.run.workflow_yaml) return;
    navigate(`/business/${detail.run.run_id}`, {
      state: {
        attach: true,
        workflowYaml: detail.run.workflow_yaml,
        workflowName: detail.run.workflow_name,
      },
    });
  }

  async function autofixAndOpenInBuilder() {
    if (!detail?.run.workflow_yaml) return;
    setAutofixBusy(true);
    setAutofixErr(null);
    try {
      const result = await api.autofixWorkflow(detail.run.workflow_yaml);
      navigate('/builder', { state: { generatedYaml: result.yaml } });
    } catch (error) {
      setAutofixErr(String(error));
    } finally {
      setAutofixBusy(false);
    }
  }

  async function pauseRun() {
    if (!detail || detail.run.status !== 'running') return;
    setActionErr(null);
    setActionBusy('pause');
    try {
      await api.pauseRun(detail.run.run_id);
    } catch (error) {
      setActionErr(String(error));
    } finally {
      setActionBusy(null);
    }
  }

  async function resumeRun() {
    if (!detail || detail.run.status !== 'paused') return;
    setActionErr(null);
    setActionBusy('resume');
    try {
      await api.resumePausedRun(detail.run.run_id);
    } catch (error) {
      setActionErr(String(error));
    } finally {
      setActionBusy(null);
    }
  }

  async function restartRun() {
    if (!detail) return;
    setActionErr(null);
    setActionBusy('restart');
    try {
      const newRunId = crypto.randomUUID();
      await api.restartRun(detail.run.run_id, newRunId);
      navigate(`/history/${newRunId}`);
    } catch (error) {
      setActionErr(String(error));
    } finally {
      setActionBusy(null);
    }
  }

  async function deleteRun() {
    if (!detail) return;
    setActionErr(null);
    setActionBusy('delete');
    try {
      const deletedRunId = detail.run.run_id;
      await api.deleteRun(deletedRunId);
      setRuns((prev) => prev.filter((r) => r.run_id !== deletedRunId));
      setDetail(null);
      navigate('/history', { replace: true });
    } catch (error) {
      setActionErr(String(error));
    } finally {
      setActionBusy(null);
    }
  }

  // The backend 409s a blocked delete with this exact wording (see
  // app/api/runs.py delete_run_endpoint) — matched here only to offer a
  // one-click way to unstick it, not as a structured error contract.
  const blockingPipelineId = actionErr?.match(/active stage of pipeline '([^']+)'/)?.[1] ?? null;

  async function abandonBlockingPipelineAndDelete() {
    if (!blockingPipelineId) return;
    setActionErr(null);
    setActionBusy('delete');
    try {
      await api.abandonPipeline(blockingPipelineId);
      await deleteRun();
    } catch (error) {
      setActionErr(String(error));
      setActionBusy(null);
    }
  }

  return {
    runs,
    listErr,
    refresh,
    detail,
    detailErr,
    retryErr,
    actionErr,
    actionBusy,
    blockingPipelineId,
    retryFailedRun,
    openInCockpit,
    openInBusinessView,
    autofixBusy,
    autofixErr,
    autofixAndOpenInBuilder,
    pauseRun,
    resumeRun,
    restartRun,
    deleteRun,
    abandonBlockingPipelineAndDelete,
    navigate,
  };
}
