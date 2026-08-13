import { useEffect, useState } from 'react';
import { knowledgeApi, type CollectionResource } from '../../api/knowledge';
import { ErrorNotice, ResourceId, Status } from './shared';

export function CollectionsPage() {
  const [collections, setCollections] = useState<CollectionResource[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [docTypes, setDocTypes] = useState('general');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function refresh() {
    knowledgeApi.listCollections().then(setCollections).catch(err => setError(String(err)));
  }
  useEffect(refresh, []);

  async function create() {
    setCreating(true); setError(null);
    try {
      await knowledgeApi.createCollection({
        name,
        description,
        doc_types: docTypes.split(',').map(value => value.trim()).filter(Boolean),
      });
      setName(''); setDescription('');
      refresh();
    } catch (err) { setError(String(err)); } finally { setCreating(false); }
  }

  return <div className="space-y-5">
    <section className="ui-card p-5 space-y-4">
      <h3 className="font-semibold">Create Collection</h3>
      <p className="text-sm text-ink-500">A Collection is the unit of "what knowledge exists" — its own metadata schema, documents, indexes and active version.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-ink-600">Name<input className="ui-input mt-1 w-full" value={name} onChange={event => setName(event.target.value)} placeholder="Dura 25 Product Knowledge" /></label>
        <label className="text-xs text-ink-600">Document types (comma-separated)<input className="ui-input mt-1 w-full" value={docTypes} onChange={event => setDocTypes(event.target.value)} /></label>
      </div>
      <label className="block text-xs text-ink-600">Description<textarea className="ui-input mt-1 w-full min-h-16" value={description} onChange={event => setDescription(event.target.value)} /></label>
      <div className="flex justify-end"><button type="button" className="ui-button ui-button--primary" disabled={creating || !name.trim()} onClick={() => void create()}>{creating ? 'Creating…' : 'Create collection'}</button></div>
    </section>
    <ErrorNotice error={error} />
    <section className="ui-card p-5">
      <h3 className="mb-4 font-semibold">Collections</h3>
      <div className="space-y-2">
        {collections.length === 0 && <p className="text-sm text-ink-500">No collections yet — create one above.</p>}
        {collections.map(item => <div key={item.collection_id} className="grid gap-2 rounded-lg border border-slate-200 p-4 sm:grid-cols-[1fr_auto] sm:items-center">
          <div>
            <div className="flex items-center gap-2"><b>{item.name}</b><Status value={item.status} /></div>
            {item.description && <p className="mt-1 text-xs text-ink-500">{item.description}</p>}
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-ink-500">
              <span>{item.document_count} documents</span><span>{item.chunk_count} chunks</span>
              <span>Active index: {item.active_index_id ? <ResourceId value={item.active_index_id} /> : 'none'}</span>
            </div>
          </div>
          <ResourceId value={item.collection_id} />
        </div>)}
      </div>
    </section>
  </div>;
}
