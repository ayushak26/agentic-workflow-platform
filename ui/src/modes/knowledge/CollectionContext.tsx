import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { knowledgeApi } from '../../api/knowledge';
import type { CollectionResource } from '../../api/knowledge';
import { ResourceId, Status } from './shared';
import {
  COLLECTION_STORAGE_KEY,
  CollectionContext,
  useCollection,
  type CollectionContextValue,
} from './collectionStore';

export function CollectionProvider({ children }: { children: ReactNode }) {
  const [collections, setCollections] = useState<CollectionResource[]>([]);
  const [collectionId, setCollectionIdState] = useState<string>(
    () => window.localStorage.getItem(COLLECTION_STORAGE_KEY) ?? '',
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const setCollectionId = useCallback((id: string) => {
    setCollectionIdState(id);
    if (id) window.localStorage.setItem(COLLECTION_STORAGE_KEY, id);
    else window.localStorage.removeItem(COLLECTION_STORAGE_KEY);
  }, []);

  // Applies a freshly fetched list, keeping the stored choice when it still
  // exists so a reload or tab switch never silently moves the user elsewhere.
  const apply = useCallback((values: CollectionResource[]) => {
    setCollections(values);
    setError(null);
    setCollectionIdState(current => {
      const keep = current && values.some(item => item.collection_id === current);
      const next = keep ? current : values[0]?.collection_id ?? '';
      if (next) window.localStorage.setItem(COLLECTION_STORAGE_KEY, next);
      else window.localStorage.removeItem(COLLECTION_STORAGE_KEY);
      return next;
    });
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      apply(await knowledgeApi.listCollections());
    } catch (err) {
      setError(String(err));
    } finally {
      setLoading(false);
    }
  }, [apply]);

  useEffect(() => {
    let cancelled = false;
    knowledgeApi.listCollections()
      .then(values => { if (!cancelled) apply(values); })
      .catch(err => { if (!cancelled) setError(String(err)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [apply]);

  const value = useMemo<CollectionContextValue>(() => ({
    collections,
    collectionId,
    collection: collections.find(item => item.collection_id === collectionId) ?? null,
    setCollectionId,
    refresh,
    loading,
    error,
  }), [collections, collectionId, setCollectionId, refresh, loading, error]);

  return <CollectionContext.Provider value={value}>{children}</CollectionContext.Provider>;
}

/**
 * The always-visible answer to "which collection am I working in?".
 * Rendered once by KnowledgeRoot, above the tab content.
 */
export function CollectionBar({ onCreate }: { onCreate: () => void }) {
  const { collections, collectionId, collection, setCollectionId, loading } = useCollection();

  if (loading) {
    return <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4 text-sm text-ink-500">Loading collections…</div>;
  }

  if (collections.length === 0) {
    return <div className="mb-5 rounded-xl border border-amber-200 bg-amber-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-sm font-semibold text-amber-900">No collections yet</div>
          <p className="mt-1 text-xs text-amber-800">A Collection holds your documents, indexes and active version. Everything else in Knowledge Studio needs one.</p>
        </div>
        <button type="button" className="ui-button ui-button--primary" onClick={onCreate}>Create the first collection</button>
      </div>
    </div>;
  }

  return <div className="mb-5 rounded-xl border border-slate-200 bg-white p-4">
    <div className="flex flex-wrap items-end gap-x-6 gap-y-3">
      <label className="text-[11px] uppercase tracking-wide text-ink-400">
        Working in
        <select
          className="ui-input mt-1 block w-64 font-medium"
          value={collectionId}
          onChange={event => setCollectionId(event.target.value)}
        >
          {collections.map(item => <option key={item.collection_id} value={item.collection_id}>{item.name}</option>)}
        </select>
      </label>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-ink-400">Collection ID</div>
        <div className="mt-1"><ResourceId value={collectionId} /></div>
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-ink-400">Status</div>
        <div className="mt-1.5"><Status value={collection?.status ?? 'draft'} /></div>
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wide text-ink-400">Content</div>
        <div className="mt-1.5 text-sm text-ink-700"><b>{collection?.document_count ?? 0}</b> docs · <b>{collection?.chunk_count ?? 0}</b> chunks</div>
      </div>
      <div className="min-w-0">
        <div className="text-[11px] uppercase tracking-wide text-ink-400">Active index (searched)</div>
        <div className="mt-1">{collection?.active_index_id
          ? <ResourceId value={collection.active_index_id} />
          : <span className="text-xs text-amber-700">none — activate an index to make this collection searchable</span>}</div>
      </div>
    </div>
  </div>;
}
