import { useEffect, useState } from 'react';
import { knowledgeApi, type DocTypeChoice } from '../../api/knowledge';
import { ErrorNotice, ResourceId, Status } from './shared';
import { useCollection } from './collectionStore';

export function CollectionsPage() {
  const { collections, collectionId, setCollectionId, refresh } = useCollection();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [choices, setChoices] = useState<DocTypeChoice[]>([]);
  const [docTypes, setDocTypes] = useState<string[]>(['general']);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    knowledgeApi.docTypes().then(r => setChoices(r.doc_types)).catch(() => setChoices([]));
  }, []);

  function toggle(id: string) {
    setDocTypes(current => current.includes(id)
      ? current.filter(value => value !== id)
      : [...current, id]);
  }

  const precision = choices.filter(item => item.precision_sensitive && docTypes.includes(item.id));

  async function create() {
    setCreating(true); setError(null);
    try {
      const created = await knowledgeApi.createCollection({
        name,
        description,
        doc_types: docTypes.length > 0 ? docTypes : ['general'],
      });
      setName(''); setDescription('');
      // Make the new collection the one every other tab acts on, so the next
      // step (Ingestion) is already pointed at what was just created.
      setCollectionId(created.collection_id);
      await refresh();
    } catch (err) { setError(String(err)); } finally { setCreating(false); }
  }

  return <div className="space-y-5">
    <section className="ui-card p-5 space-y-4">
      <h3 className="font-semibold">Create Collection</h3>
      <p className="text-sm text-ink-500">A Collection is the unit of "what knowledge exists" — its own metadata schema, documents, indexes and active version.</p>
      <label className="block text-xs text-ink-600">Name<input className="ui-input mt-1 w-full max-w-md" value={name} onChange={event => setName(event.target.value)} placeholder="Dura 25 Product Knowledge" /></label>
      <label className="block text-xs text-ink-600">Description<textarea className="ui-input mt-1 w-full min-h-16" value={description} onChange={event => setDescription(event.target.value)} /></label>
      <fieldset>
        <legend className="text-xs text-ink-600">What kind of documents will this hold?</legend>
        <p className="mt-1 text-[11px] text-ink-400">Used to filter retrieval by document type, and to choose the embedding model when ingestion is set to Auto.</p>
        <div className="mt-2 flex flex-wrap gap-2">
          {choices.map(item => {
            const on = docTypes.includes(item.id);
            return <button
              key={item.id}
              type="button"
              title={item.description}
              aria-pressed={on}
              onClick={() => toggle(item.id)}
              className={`rounded-full border px-3 py-1 text-xs ${on
                ? 'border-accent-400 bg-accent-50 font-medium text-accent-800'
                : 'border-slate-200 bg-white text-ink-600 hover:bg-slate-50'}`}
            >{item.label}{item.precision_sensitive && <span className="ml-1 text-[10px] text-ink-400">◆</span>}</button>;
          })}
        </div>
        {precision.length > 0 && <p className="mt-2 text-[11px] text-ink-500">◆ {precision.map(item => item.label).join(', ')} — precision-sensitive, so Auto will pick the highest-quality embedding model.</p>}
      </fieldset>
      <div className="flex items-center justify-end gap-3">
        {!name.trim() && <p className="text-xs text-ink-500">Give the collection a name to continue.</p>}
        <button type="button" className="ui-button ui-button--primary" disabled={creating || !name.trim()} onClick={() => void create()}>{creating ? 'Creating…' : 'Create collection'}</button>
      </div>
    </section>
    <ErrorNotice error={error} />
    <section className="ui-card p-5">
      <h3 className="mb-4 font-semibold">Collections</h3>
      <div className="space-y-2">
        {collections.length === 0 && <p className="text-sm text-ink-500">No collections yet — create one above.</p>}
        {collections.map(item => <div key={item.collection_id} className={`grid gap-2 rounded-lg border p-4 sm:grid-cols-[1fr_auto] sm:items-center ${item.collection_id === collectionId ? 'border-accent-400 bg-accent-50/40' : 'border-slate-200'}`}>
          <div>
            <div className="flex items-center gap-2"><b>{item.name}</b><Status value={item.status} />{item.collection_id === collectionId
              ? <span className="rounded-full bg-accent-100 px-2 py-1 text-[11px] font-medium text-accent-700">working here</span>
              : <button type="button" className="text-[11px] text-accent-700 underline" onClick={() => setCollectionId(item.collection_id)}>Work in this</button>}</div>
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
