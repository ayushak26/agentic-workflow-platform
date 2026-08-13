import { useEffect, useState } from 'react';
import { knowledgeApi, type RetrievalTraceSummary } from '../../api/knowledge';
import { ErrorNotice, ResourceId } from './shared';

export function TracesPage() {
  const [traces, setTraces] = useState<RetrievalTraceSummary[]>([]);
  const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => { knowledgeApi.listTraces(100).then(setTraces).catch(err => setError(String(err))); }, []);

  async function open(id: string) {
    setLoading(true); setError(null);
    try { setSelected(await knowledgeApi.getTrace(id)); }
    catch (err) { setError(String(err)); } finally { setLoading(false); }
  }

  return <div className="space-y-5">
    <ErrorNotice error={error} />
    <section className="ui-card p-5">
      <h3 className="mb-4 font-semibold">Retrieval Traces</h3>
      <p className="mb-4 text-sm text-ink-500">Every search — from RAG Agents, KnowledgeRetrieval nodes and the Playground — leaves an exact, scoped trace here.</p>
      <div className="space-y-2">
        {traces.length === 0 && <p className="text-sm text-ink-500">No retrieval traces yet.</p>}
        {traces.map(item => <button
          type="button" key={item.retrieval_request_id}
          onClick={() => void open(item.retrieval_request_id)}
          className="grid w-full gap-2 rounded-lg border border-slate-200 p-4 text-left sm:grid-cols-[1fr_auto] sm:items-center hover:bg-slate-50"
        >
          <div>
            <p className="line-clamp-1 text-sm">{item.original_query}</p>
            <div className="mt-1 flex flex-wrap gap-3 text-xs text-ink-500"><span>{Math.round(item.timings_ms.total_ms ?? 0)} ms</span><span>{item.status}</span><span>{item.created_at}</span></div>
          </div>
          <ResourceId value={item.retrieval_request_id} />
        </button>)}
      </div>
    </section>
    {loading && <p className="text-sm text-ink-500">Loading trace…</p>}
    {selected && <section className="ui-card p-5">
      <div className="flex items-center justify-between"><h3 className="font-semibold">Trace detail</h3><ResourceId value={String(selected.retrieval_request_id ?? '')} /></div>
      <pre className="mt-4 max-h-[32rem] overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-4 text-[11px] text-slate-100">{JSON.stringify(selected, null, 2)}</pre>
    </section>}
  </div>;
}
