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

    const schedule = () => {
      if (!cancelled) timer = window.setTimeout(() => void load(), 2500);
    };
    const load = async () => {
      let keepPolling = true;
      try {
        const data = await api.runHistory(RUN_LIST_LIMIT);
        if (cancelled) return;
        // Selection is tracked by stable run_id in the URL, not array index or
        // object identity, so replacing the list cannot lose the selection.
        setRuns(data.runs);
        setListErr(null);
      } catch (error) {
        if (cancelled) return;
        const msg = String(error);
        // On auth failure, try to recover the cookie-backed session once. A
        // failed recovery stops polling instead of hammering the endpoint.
        if (msg.includes('401')) {
          const user = await rehydrate();
          if (!cancelled && !user) {
            keepPolling = false;
            setListErr('Session expired — please log in again.');
          }
        } else {
          setListErr(msg);
        }
      } finally {
        if (keepPolling) schedule();
      }
    };

    void load();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [refreshToken]);

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    // Clear the previous route's detail before synchronizing the new run.
    setDetail(null);
    setDetailErr(null);
    setRetryErr(null);
    setActionErr(null);
    let timer: number | undefined;
    const schedule = () => {
      if (!cancelled) timer = window.setTimeout(() => void load(), 2000);
    };
    const load = async () => {
      let terminal = false;
      try {
        const data = await api.runDetail(runId);
        if (cancelled) return;
        setDetail(data);
        setDetailErr(null);
        terminal = TERMINAL_STATUSES.has(data.run.status);
      } catch (error) {
        if (!cancelled) setDetailErr(String(error));
      } finally {
        if (!terminal) schedule();
      }
    };
    void load();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
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
      navigate(`/workflow-runs/${newRunId}`);
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
      navigate('/workflow-runs', { replace: true });
    } catch (error) {
      setActionErr(String(error));
    } finally {
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
    retryFailedRun,
    openInCockpit,
    autofixBusy,
    autofixErr,
    autofixAndOpenInBuilder,
    pauseRun,
    resumeRun,
    restartRun,
    deleteRun,
    navigate,
  };
}
