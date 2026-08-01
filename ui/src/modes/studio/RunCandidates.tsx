import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  api,
  type DiscoveredCandidate,
  type InternalEvidenceRecord,
  type RunCandidate,
} from '../../api/client';

const foundByLabel: Record<string, string> = {
  ScholarlyCandidateDiscoveryAgent: 'Scholarly discovery',
  BoundedDeepResearchAgent: 'Deep Research',
  PriorProjectRetrieverAgent: 'Prior-project search (CORDIS/LIFE/EIP-AGRI)',
  StructuredDatasetRetrieverAgent: 'Official database (Eurostat)',
};

const sourceTypeLabel: Record<string, string> = {
  website: 'Website',
  book: 'Book',
  citation: 'Citation',
  unknown: 'Unknown',
};

export function RunCandidates() {
  const { runId } = useParams<{ runId?: string }>();
  const navigate = useNavigate();
  const [candidates, setCandidates] = useState<RunCandidate[] | null>(null);
  const [discovered, setDiscovered] = useState<DiscoveredCandidate[] | null>(null);
  const [internalEvidence, setInternalEvidence] = useState<InternalEvidenceRecord[] | null>(null);
  const [verifying, setVerifying] = useState<string | null>(null);
  const [verifyError, setVerifyError] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const res = await api.runCandidates(runId as string);
        if (!cancelled) {
          setCandidates(res.candidates);
          setDiscovered(res.discovered_candidates);
          setInternalEvidence(res.internal_evidence);
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [runId]);

  async function verifyClaim(recordId: string) {
    if (!runId) return;
    setVerifying(recordId);
    setVerifyError((prev) => ({ ...prev, [recordId]: '' }));
    try {
      const res = await api.verifyClaim(runId, recordId);
      setInternalEvidence(
        (prev) =>
          prev?.map((item) =>
            item.record_id === recordId ? { ...item, verification: res.result } : item,
          ) ?? prev,
      );
    } catch (e) {
      setVerifyError((prev) => ({ ...prev, [recordId]: String(e) }));
    } finally {
      setVerifying(null);
    }
  }

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

        <div className="mt-8">
          <h3 className="text-sm font-semibold text-ink-900">Discovered evidence</h3>
          <p className="text-xs text-ink-500 mt-1">
            Every source found by scholarly discovery or Deep Research for this
            run — web, database, and research-paper URLs alike — deduplicated
            by candidate id. These are candidates, not verified evidence.
          </p>
          {!error && discovered && discovered.length === 0 && (
            <div className="text-sm text-ink-500 mt-3">
              No discovery or Deep Research candidates recorded for this run.
            </div>
          )}
          {!error && discovered && discovered.length > 0 && (
            <div className="border border-slate-200 rounded-md overflow-hidden bg-white mt-3">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-ink-600">
                  <tr>
                    <th className="text-left font-medium px-3 py-2">Name</th>
                    <th className="text-left font-medium px-3 py-2">URL</th>
                    <th className="text-left font-medium px-3 py-2">Claim</th>
                    <th className="text-left font-medium px-3 py-2">Found via</th>
                    <th className="text-left font-medium px-3 py-2">Authority</th>
                  </tr>
                </thead>
                <tbody>
                  {discovered.map((d) => (
                    <tr key={d.candidate_id} className="border-t border-slate-100 align-top">
                      <td className="px-3 py-2 max-w-sm">{d.title}</td>
                      <td className="px-3 py-2 max-w-sm break-all">
                        {d.url ? (
                          <a
                            href={d.url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-accent-600 hover:underline"
                          >
                            {d.url}
                          </a>
                        ) : (
                          <span className="text-ink-400">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-ink-500">
                        {d.claim_id || '—'}
                      </td>
                      <td className="px-3 py-2">
                        {foundByLabel[d.found_by_type] ?? d.found_by_type}
                      </td>
                      <td className="px-3 py-2">{d.authority || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="mt-8">
          <h3 className="text-sm font-semibold text-ink-900">
            Internal evidence — needs source verification
          </h3>
          <p className="text-xs text-ink-500 mt-1">
            Partner and internal-document facts have no public URL — only a
            source name and an exact quoted passage. Use "Verify this claim"
            to have gpt-5.6-sol run a web search and report whether it can
            corroborate the claim and name a specific source. This is a
            secondary check for a human reviewer; it never changes whether
            the record is drafting-allowed.
          </p>
          {!error && internalEvidence && internalEvidence.length === 0 && (
            <div className="text-sm text-ink-500 mt-3">
              No internal-evidence records recorded for this run.
            </div>
          )}
          {!error && internalEvidence && internalEvidence.length > 0 && (
            <div className="border border-slate-200 rounded-md overflow-hidden bg-white mt-3">
              <table className="w-full text-sm">
                <thead className="bg-slate-50 text-ink-600">
                  <tr>
                    <th className="text-left font-medium px-3 py-2">Content</th>
                    <th className="text-left font-medium px-3 py-2">Source</th>
                    <th className="text-left font-medium px-3 py-2">
                      Link (verify this claim)
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {internalEvidence.map((rec) => {
                    const v = rec.verification;
                    const isVerifying = verifying === rec.record_id;
                    const err = verifyError[rec.record_id];
                    return (
                      <tr key={rec.record_id} className="border-t border-slate-100 align-top">
                        <td className="px-3 py-2 max-w-md">
                          <div>{rec.content}</div>
                          {rec.fact_key && (
                            <div className="text-xs text-ink-400 mt-0.5 font-mono">
                              {rec.fact_key}
                            </div>
                          )}
                        </td>
                        <td className="px-3 py-2 max-w-xs">
                          <div className="font-mono text-xs">{rec.source_name || '—'}</div>
                          <div className="text-xs text-ink-400 mt-0.5">
                            {rec.source_class || 'unclassified'}
                          </div>
                        </td>
                        <td className="px-3 py-2 max-w-sm">
                          <button
                            onClick={() => void verifyClaim(rec.record_id)}
                            disabled={isVerifying}
                            className="px-2.5 py-1 rounded border border-slate-300 text-xs text-ink-700 hover:bg-slate-50 disabled:opacity-50"
                          >
                            {isVerifying
                              ? 'Verifying…'
                              : v
                                ? 'Re-verify this claim'
                                : 'Verify this claim'}
                          </button>
                          {err && <div className="text-xs text-red-700 mt-1">{err}</div>}
                          {v && (
                            <div className="mt-2 text-xs space-y-0.5">
                              <div
                                className={
                                  v.verified ? 'text-green-700 font-medium' : 'text-ink-500 font-medium'
                                }
                              >
                                {v.verified ? 'Corroborated' : 'Not corroborated'} ·{' '}
                                {v.confidence} confidence
                              </div>
                              <div className="text-ink-500">
                                Source: {sourceTypeLabel[v.source_type] ?? v.source_type}
                                {v.source_name ? ` — ${v.source_name}` : ''}
                              </div>
                              {v.source_url && (
                                <a
                                  href={v.source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="text-accent-600 hover:underline break-all block"
                                >
                                  {v.source_url}
                                </a>
                              )}
                              {v.citation && (
                                <div className="text-ink-500 italic">{v.citation}</div>
                              )}
                              {v.notes && <div className="text-ink-400">{v.notes}</div>}
                            </div>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
