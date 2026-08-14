import { useEffect, useState } from 'react';
import { knowledgeApi, type DocumentResource, type IndexVersion } from '../../api/knowledge';
import { ErrorNotice, ResourceId, Status } from './shared';
import { useCollection } from './collectionStore';

export function DocumentsIndexesPage() {
  const { collectionId, collection, refresh: refreshCollections } = useCollection();
  const [documents, setDocuments] = useState<DocumentResource[]>([]);
  const [indexes, setIndexes] = useState<IndexVersion[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [activating, setActivating] = useState<string | null>(null);

  function refresh(id: string) {
    if (!id) return;
    Promise.all([knowledgeApi.listDocuments(id), knowledgeApi.listIndexes(id)])
      .then(([docs, idx]) => { setDocuments(docs); setIndexes(idx); })
      .catch(err => setError(String(err)));
  }
  // KnowledgeRoot keys this page by collection, so a switch remounts it with
  // empty state rather than briefly showing the previous collection's rows.
  useEffect(() => { refresh(collectionId); }, [collectionId]);

  async function activate(indexId: string) {
    setActivating(indexId); setError(null);
    try {
      await knowledgeApi.activateIndex(collectionId, indexId);
      await refreshCollections();
      refresh(collectionId);
    } catch (err) { setError(String(err)); } finally { setActivating(null); }
  }

  const activeIndexId = collection?.active_index_id;

  return <div className="space-y-5">
    <ErrorNotice error={error} />
    <section className="ui-card p-5">
      <h3 className="mb-4 font-semibold">Indexes</h3>
      <div className="space-y-2">
        {indexes.length === 0 && <p className="text-sm text-ink-500">No indexes yet — run an ingestion first.</p>}
        {indexes.map(item => <div key={item.index_id} className="grid gap-2 rounded-lg border border-slate-200 p-4 sm:grid-cols-[1fr_auto_auto] sm:items-center">
          <div>
            <div className="flex items-center gap-2"><b>v{item.version}</b><Status value={item.status} />{item.index_id === activeIndexId && <span className="rounded-full bg-accent-100 px-2 py-1 text-[11px] font-medium text-accent-700">active</span>}</div>
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink-500"><span>{item.document_count} documents</span><span>{item.chunk_count} chunks</span></div>
          </div>
          <ResourceId value={item.index_id} />
          <button type="button" className="ui-button ui-button--secondary" disabled={item.index_id === activeIndexId || activating === item.index_id || item.status === 'failed' || item.status === 'building'} onClick={() => void activate(item.index_id)}>{activating === item.index_id ? 'Activating…' : 'Activate'}</button>
        </div>)}
      </div>
    </section>
    <section className="ui-card p-5">
      <h3 className="mb-4 font-semibold">Documents</h3>
      <div className="space-y-2">
        {documents.length === 0 && <p className="text-sm text-ink-500">No documents in this collection yet.</p>}
        {documents.map(item => <div key={item.document_id} className="grid gap-2 rounded-lg border border-slate-200 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
          <div>
            <div className="flex items-center gap-2"><b>{item.filename}</b><Status value={item.status} /><span className="text-xs text-ink-400">{item.source_format}</span></div>
            {item.error && <p className="mt-1 text-xs text-rose-600">{item.error}</p>}
          </div>
          <ResourceId value={item.document_id} />
        </div>)}
      </div>
    </section>
  </div>;
}
