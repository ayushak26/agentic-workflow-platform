import { useEffect, useState } from 'react';

import { knowledgeApi } from '../../../api/knowledge';

type Option = { id: string; label: string; status?: string };

/**
 * Selector for config fields the backend marks with `x-resource`
 * (KnowledgeRetrieval's collection_id / retrieval_profile_id). Loads the
 * real registry from Knowledge Studio instead of making the author type a
 * raw identifier; blocks editing when the registry cannot be loaded, and
 * flags values that no longer resolve (a deleted or
 * renamed knowledge base would otherwise fail only at run time).
 */
export function ResourceSelect({
  resource,
  value,
  onChange,
}: {
  resource: 'collection' | 'retrieval_profile';
  value: string;
  onChange: (next: string) => void;
}) {
  const [options, setOptions] = useState<Option[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = resource === 'collection'
      ? knowledgeApi.listCollections().then(items => items.map(item => ({
          id: item.collection_id,
          label: `${item.name} (${item.document_count} docs${
            item.status === 'active' || item.status === 'ready' ? '' : `, ${item.status}`
          })`,
          status: item.status,
        })))
      : knowledgeApi.listProfiles('retrieval').then(items => items.map(item => ({
          id: item.profile_id,
          label: `${item.name} (v${item.version})`,
          status: item.status,
        })));
    load
      .then(items => { if (!cancelled) setOptions(items); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [resource]);

  const selectClass = 'mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border';

  if (failed) {
    return (
      <div className="mt-1 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-800">
        Knowledge Studio could not be reached. Existing selection
        {value ? ` (${value})` : ''} was preserved; retry before changing it.
      </div>
    );
  }

  if (options === null) {
    return <div className="mt-1 text-xs text-ink-500">Loading from Knowledge Studio…</div>;
  }

  const missing = value !== '' && !options.some(item => item.id === value);

  return (
    <div>
      <select
        value={value}
        onChange={e => onChange(e.target.value)}
        className={selectClass}
      >
        <option value="">Select…</option>
        {missing && (
          <option value={value}>
            {value} (not found in Knowledge Studio)
          </option>
        )}
        {options.map(item => (
          <option
            disabled={item.status !== 'active' && item.status !== 'ready'}
            key={item.id}
            value={item.id}
          >
            {item.label}
          </option>
        ))}
      </select>
      {missing && (
        <p className="mt-1 text-xs text-amber-700">
          The selected {resource === 'collection' ? 'knowledge base' : 'retrieval profile'}{' '}
          no longer exists in Knowledge Studio. Pick another one or this workflow will fail
          validation.
        </p>
      )}
    </div>
  );
}
