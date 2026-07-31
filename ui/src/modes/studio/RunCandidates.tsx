import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api, type RunCandidate } from '../../api/client';

export function RunCandidates() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();
  const [candidates, setCandidates] = useState<RunCandidate[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    api
      .runCandidates(runId)
      .then((res) => setCandidates(res.candidates))
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [runId]);

  const shown = (candidates ?? []).filter((c) =>
    filter.trim() === ''
      ? true
      : `${c.source_id} ${c.version_id} ${c.key}`
          .toLowerCase()
          .includes(filter.toLowerCase()),
  );

  const fmtSize = (n: number) =>
    n >= 1_000_000 ? `${(n / 1_000_000).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

  return (
    <div className="h-full flex flex-col">
      <header className="px-6 py-4 border-b border-slate-200 bg-white">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-lg font-semibold text-ink-900">Evidence candidates</h2>
            <div className="text-xs text-ink-500 mt-1 font-mono">{runId}</div>
            <div className="text-xs text-ink-500 mt-1">
              {loading
                ? 'Loading…'
                : candidates
                  ? `${candidates.length} acquired source${candidates.length === 1 ? '' : 's'}`
                  : ''}
            </div>
          </div>
          <button
            onClick={() => navigate('/history')}
            className="flex-none px-3 py-1.5 rounded-md border border-slate-300 text-sm text-ink-700 hover:bg-slate-50"
          >
            Back
          </button>
        </div>
        <div className="mt-3">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by source or version id…"
            className="w-full max-w-md border border-slate-300 rounded-md px-3 py-1.5 text-sm"
          />
        </div>
      </header>

      <div className="flex-1 overflow-y-auto p-6">
        {error && (
          <div className="rounded-md border border-red-300 bg-red-50 text-red-800 text-sm p-3">
            {error}
          </div>
        )}
        {!error && candidates && candidates.length === 0 && (
          <div className="text-sm text-ink-500">
            No evidence found for this run in object storage.
          </div>
        )}
        {!error && candidates && candidates.length > 0 && (
          <div className="border border-slate-200 rounded-md overflow-hidden bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-ink-600">
                <tr>
                  <th className="text-left font-medium px-3 py-2">#</th>
                  <th className="text-left font-medium px-3 py-2">Source</th>
                  <th className="text-left font-medium px-3 py-2">Version</th>
                  <th className="text-left font-medium px-3 py-2">Size</th>
                  <th className="text-left font-medium px-3 py-2">Open</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((c, i) => (
                  <tr key={c.key} className="border-t border-slate-100">
                    <td className="px-3 py-2 text-ink-400">{i + 1}</td>
                    <td className="px-3 py-2 font-mono text-xs">{c.source_id}</td>
                    <td className="px-3 py-2 font-mono text-xs text-ink-500">{c.version_id}</td>
                    <td className="px-3 py-2 text-ink-500">{fmtSize(c.size)}</td>
                    <td className="px-3 py-2">
                      <a
                        href={api.fileUrl(c.key)}
                        target="_blank"
                        rel="noreferrer"
                        className="text-accent-600 hover:underline font-medium"
                      >
                        Open PDF
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
