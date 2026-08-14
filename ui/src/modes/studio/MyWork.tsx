import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { RunSummary } from '../../api/types';

type Bucket = 'attention' | 'running' | 'paused' | 'exceptions' | 'completed';

const BUCKET_META: Record<Bucket, { title: string; hint: string }> = {
  attention: { title: 'Needs Your Attention', hint: 'Waiting on a decision from you' },
  running: { title: 'Running', hint: 'In progress right now' },
  paused: { title: 'Paused', hint: 'Stopped by request — resume when ready' },
  exceptions: { title: 'Exceptions', hint: 'Failed or rejected — needs a look' },
  completed: { title: 'Recently Completed', hint: 'Finished work, available for reference' },
};

const BUCKET_ORDER: Bucket[] = ['attention', 'exceptions', 'running', 'paused', 'completed'];

function bucketFor(run: RunSummary): Bucket {
  if (run.status === 'paused') {
    return run.pause_kind === 'user_requested' ? 'paused' : 'attention';
  }
  if (run.status === 'failed' || run.status === 'rejected') return 'exceptions';
  if (run.status === 'running') return 'running';
  return 'completed';
}

function humanizeWorkflowName(name: string): string {
  const spaced = name.replace(/[_-]+/g, ' ').trim();
  if (!spaced) return 'Work item';
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function elapsedLabel(startedAt: number | null): string {
  if (startedAt == null) return '';
  const seconds = Math.max(0, Date.now() / 1000 - startedAt);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.floor(hours / 24)} d ago`;
}

function WorkItemCard({
  run,
  subtitle,
  onOpen,
}: {
  run: RunSummary;
  subtitle: string | null;
  onOpen: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onOpen}
      className="w-full rounded-lg border border-slate-200 bg-white p-3 text-left transition hover:border-accent-300 hover:shadow-sm"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-ink-400">#{run.run_id.slice(0, 8)}</span>
        <span className="text-xs text-ink-400">{elapsedLabel(run.started_at)}</span>
      </div>
      <div className="mt-1 text-sm font-medium text-ink-900">{humanizeWorkflowName(run.workflow_name)}</div>
      {subtitle && <div className="mt-1 text-sm text-ink-500">{subtitle}</div>}
    </button>
  );
}

export function MyWork() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [attentionSubtitles, setAttentionSubtitles] = useState<Record<string, string>>({});

  useEffect(() => {
    let cancelled = false;
    api.runHistory(100)
      .then(data => { if (!cancelled) setRuns(data.runs); })
      .catch(e => { if (!cancelled) setError(e instanceof Error ? e.message : String(e)); });
    return () => { cancelled = true; };
  }, []);

  const buckets = useMemo(() => {
    const grouped: Record<Bucket, RunSummary[]> = {
      attention: [], running: [], paused: [], exceptions: [], completed: [],
    };
    for (const run of runs ?? []) {
      grouped[bucketFor(run)].push(run);
    }
    // Most-recently-updated first within each bucket, and cap "completed" so
    // finished work doesn't bury what actually needs a decision.
    for (const bucket of BUCKET_ORDER) {
      grouped[bucket].sort((a, b) => (b.started_at ?? 0) - (a.started_at ?? 0));
    }
    grouped.completed = grouped.completed.slice(0, 8);
    return grouped;
  }, [runs]);

  // Fetch the actual pending question for items that need attention — a
  // bounded set, so this stays cheap even though it's one call per item.
  useEffect(() => {
    const attentionRuns = buckets.attention;
    if (attentionRuns.length === 0) return;
    let cancelled = false;
    Promise.all(
      attentionRuns.map(run =>
        api.pendingGate(run.run_id)
          .then(gate => {
            const question = gate.paused && gate.pause_kind !== 'user_requested' ? gate.question : null;
            return [run.run_id, question || (gate.paused ? 'Waiting for your review' : null)] as const;
          })
          .catch(() => [run.run_id, null] as const),
      ),
    ).then(entries => {
      if (cancelled) return;
      const next: Record<string, string> = {};
      for (const [runId, question] of entries) {
        if (question) next[runId] = question;
      }
      setAttentionSubtitles(next);
    });
    return () => { cancelled = true; };
    // Only re-fetch when the actual set of attention-needing run ids changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [buckets.attention.map(r => r.run_id).join(',')]);

  function openRun(run: RunSummary) {
    navigate(`/business/${run.run_id}`, { state: { attach: true, workflowName: run.workflow_name } });
  }

  const totalCount = runs?.length ?? 0;
  const attentionCount = buckets.attention.length;

  return (
    <div className="h-full overflow-y-auto bg-slate-50 p-6">
      <div className="mx-auto max-w-5xl">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold text-ink-900">My Work</h1>
          <p className="mt-1 text-sm text-ink-500">
            {runs == null
              ? 'Loading your work…'
              : attentionCount > 0
              ? `${attentionCount} item${attentionCount === 1 ? '' : 's'} need your attention, ${totalCount} total.`
              : `Nothing needs you right now — ${totalCount} item${totalCount === 1 ? '' : 's'} total.`}
          </p>
        </div>

        {error && <p className="mb-4 text-sm text-bad">{error}</p>}

        {runs != null && totalCount === 0 && (
          <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-ink-500">
            No work yet. Start a process from the Library to see it here.
          </div>
        )}

        <div className="flex flex-col gap-6">
          {BUCKET_ORDER.filter(bucket => buckets[bucket].length > 0).map(bucket => (
            <section key={bucket}>
              <div className="mb-2 flex items-baseline gap-2">
                <h2 className="text-sm font-semibold uppercase tracking-wide text-ink-700">
                  {BUCKET_META[bucket].title}
                </h2>
                <span className="text-xs text-ink-400">({buckets[bucket].length})</span>
              </div>
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
                {buckets[bucket].map(run => (
                  <WorkItemCard
                    key={run.run_id}
                    run={run}
                    subtitle={
                      bucket === 'attention' ? (attentionSubtitles[run.run_id] ?? 'Waiting for your review')
                      : bucket === 'exceptions' ? (run.error ? run.error.slice(0, 90) : 'Needs a look')
                      : null
                    }
                    onOpen={() => openRun(run)}
                  />
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
    </div>
  );
}
