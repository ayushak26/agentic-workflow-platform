import { useState } from 'react';
import { api } from '../../api/client';

export function HITLPanel({
  runId,
  pausedNodeId,
  context,
  allowedActions,
  onResult,
}: {
  runId: string;
  pausedNodeId: string;
  context: unknown;
  allowedActions: string[];
  onResult: (result: any) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reason, setReason] = useState('');

  async function submit(action: 'approve' | 'reject') {
    if (busy) return; // guard against double-fire
    setBusy(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { decision: action };
      if (action === 'reject' && reason) payload.reason = reason;
      const result = await api.resumeWorkflow(runId, payload);
      onResult(result); // hand the next state up to the Cockpit
    } catch (e: any) {
      setError(String(e.message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const canReject = allowedActions.includes('reject');

  return (
    <div className="p-6">
      <div className="inline-block text-[10px] uppercase tracking-wide rounded-full px-2 py-0.5 bg-warn text-white">
        Action required
      </div>
      <h3 className="text-lg font-semibold mt-3">{pausedNodeId} is paused</h3>
      <p className="text-sm text-ink-500 mt-1">
        Review the context and approve or reject to continue the workflow.
      </p>

      <details className="mt-4">
        <summary className="text-xs font-medium text-ink-700 cursor-pointer">Pause context</summary>
        <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-3 mt-2 overflow-x-auto max-h-80 whitespace-pre-wrap">
{JSON.stringify(context, null, 2)}
        </pre>
      </details>

      <div className="mt-6 space-y-3">
        <button
          onClick={() => submit('approve')}
          disabled={busy}
          className="w-full px-4 py-2 rounded-md bg-ok text-white text-sm font-medium hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Working…' : 'Approve and continue'}
        </button>

        {canReject && (
          <>
            <div>
              <label className="block text-xs font-medium text-ink-700 mb-1">Rejection reason (optional)</label>
              <input
                type="text"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder="Why are you rejecting?"
                className="block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border"
              />
            </div>
            <button
              onClick={() => submit('reject')}
              disabled={busy}
              className="w-full px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              Reject
            </button>
          </>
        )}
      </div>

      {error && <div className="mt-3 text-sm text-bad">{error}</div>}
    </div>
  );
}