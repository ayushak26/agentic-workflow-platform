import { useEffect, useState } from 'react';

import { api } from '../../../api/client';
import type { CloudFileMeta, CloudFileRef } from '../../../api/types';

/**
 * A live folder/search browser for a connected Google Drive or OneDrive
 * account, embedded in the Integration node's configuration panel.
 *
 * Provider-neutral by design: this component only ever sees CloudFileMeta,
 * never a provider-specific shape, so the same browser works for every
 * IntegrationProvider adapter the backend registers — Dropbox/Box/SharePoint
 * later need no frontend change here.
 *
 * `onSelect` always receives an array, even in single-select mode (one
 * entry) — a uniform contract so a caller doesn't need two code paths.
 */

function formatBytes(size: number | null | undefined): string {
  if (size == null) return '—';
  if (size < 1024) return `${size} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = size / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(1)} ${units[unitIndex]}`;
}

function formatDate(value: string | null | undefined): string {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString();
}

export function CloudFileBrowser({
  connectionId,
  mode,
  multiple = false,
  onSelect,
}: {
  connectionId: string;
  mode: 'file' | 'folder';
  /** When true, rows show checkboxes and picks accumulate until "Add" is
   *  clicked; when false (default), clicking a row selects it immediately. */
  multiple?: boolean;
  onSelect: (files: CloudFileRef[]) => void;
}) {
  const [path, setPath] = useState<CloudFileMeta[]>([]);
  const [query, setQuery] = useState('');
  const [entries, setEntries] = useState<CloudFileMeta[]>([]);
  const [nextPageToken, setNextPageToken] = useState<string | undefined>();
  const [state, setState] = useState<'idle' | 'loading' | 'error'>('idle');
  const [error, setError] = useState<string | null>(null);
  const [checked, setChecked] = useState<Map<string, CloudFileRef>>(new Map());

  const currentFolderId = path.length > 0 ? path[path.length - 1].id : undefined;

  useEffect(() => {
    setEntries([]);
    setNextPageToken(undefined);
    setState('loading');
    setError(null);
    api.browseIntegrationFiles(connectionId, { folderId: currentFolderId, query })
      .then(result => {
        setEntries(result.files);
        setNextPageToken(result.next_page_token);
        setState('idle');
      })
      .catch(reason => {
        setError(reason instanceof Error ? reason.message : String(reason));
        setState('error');
      });
  }, [connectionId, currentFolderId, query]);

  const loadMore = () => {
    if (!nextPageToken) return;
    setState('loading');
    api.browseIntegrationFiles(connectionId, { folderId: currentFolderId, query, pageToken: nextPageToken })
      .then(result => {
        setEntries(previous => [...previous, ...result.files]);
        setNextPageToken(result.next_page_token);
        setState('idle');
      })
      .catch(reason => {
        setError(reason instanceof Error ? reason.message : String(reason));
        setState('error');
      });
  };

  const openFolder = (folder: CloudFileMeta) => {
    setPath(previous => [...previous, folder]);
    setQuery('');
  };

  const jumpToBreadcrumb = (index: number) => {
    setPath(previous => previous.slice(0, index + 1));
    setQuery('');
  };

  const selectable = (entry: CloudFileMeta) => (mode === 'folder' ? entry.is_folder : !entry.is_folder);

  const toggleChecked = (entry: CloudFileMeta) => {
    setChecked(previous => {
      const next = new Map(previous);
      if (next.has(entry.id)) next.delete(entry.id);
      else next.set(entry.id, { id: entry.id, name: entry.name });
      return next;
    });
  };

  const confirmChecked = () => {
    onSelect([...checked.values()]);
    setChecked(new Map());
  };

  return (
    <div className="mt-2 rounded-md border border-slate-200">
      <div className="flex items-center gap-1 border-b border-slate-200 bg-slate-50 px-2 py-1 text-[10px] text-ink-500">
        <button
          className="hover:text-accent-700 hover:underline"
          onClick={() => setPath([])}
          type="button"
        >
          Root
        </button>
        {path.map((crumb, index) => (
          <span className="flex items-center gap-1" key={crumb.id}>
            <span>/</span>
            <button
              className="hover:text-accent-700 hover:underline"
              onClick={() => jumpToBreadcrumb(index)}
              type="button"
            >
              {crumb.name}
            </button>
          </span>
        ))}
      </div>

      <div className="border-b border-slate-200 p-2">
        <input
          className="builder-field text-[11px]"
          onChange={event => setQuery(event.target.value)}
          placeholder="Search this account…"
          value={query}
        />
      </div>

      <div className="max-h-64 overflow-y-auto">
        {state === 'loading' && entries.length === 0 && (
          <div className="p-3 text-center text-[11px] text-ink-500">Loading…</div>
        )}
        {state === 'error' && (
          <div className="p-3 text-center text-[11px] text-bad">
            {error || 'Could not load this folder.'}
          </div>
        )}
        {state !== 'error' && state !== 'loading' && entries.length === 0 && (
          <div className="p-3 text-center text-[11px] text-ink-500">
            {query ? `No results for "${query}".` : 'This folder is empty.'}
          </div>
        )}
        <table className="w-full text-left text-[11px]">
          <tbody>
            {entries.map(entry => (
              <tr
                className={`border-b border-slate-100 last:border-0 ${
                  selectable(entry) && !multiple ? 'cursor-pointer hover:bg-accent-50' : ''
                }`}
                key={entry.id}
                onClick={() => {
                  if (entry.is_folder) {
                    openFolder(entry);
                    return;
                  }
                  if (!multiple && selectable(entry)) onSelect([{ id: entry.id, name: entry.name }]);
                }}
              >
                {multiple && (
                  <td className="px-2 py-1.5">
                    {selectable(entry) && (
                      <input
                        aria-label={`Select ${entry.name}`}
                        checked={checked.has(entry.id)}
                        onChange={() => toggleChecked(entry)}
                        onClick={event => event.stopPropagation()}
                        type="checkbox"
                      />
                    )}
                  </td>
                )}
                <td className="px-2 py-1.5">
                  <span className="mr-1.5">{entry.is_folder ? '📁' : '📄'}</span>
                  {entry.name}
                </td>
                <td className="px-2 py-1.5 text-ink-400">
                  {entry.is_folder ? 'Folder' : (entry.mime_type || 'File')}
                </td>
                <td className="px-2 py-1.5 text-ink-400">
                  {entry.is_folder ? '—' : formatBytes(entry.size_bytes)}
                </td>
                <td className="px-2 py-1.5 text-ink-400">{formatDate(entry.modified_at)}</td>
                <td className="px-2 py-1.5">
                  <div className="flex items-center gap-2">
                    {entry.is_folder && mode === 'folder' && !multiple && (
                      <button
                        className="text-[10px] font-medium text-accent-700 hover:underline"
                        onClick={event => {
                          event.stopPropagation();
                          onSelect([{ id: entry.id, name: entry.name }]);
                        }}
                        type="button"
                      >
                        Select
                      </button>
                    )}
                    {!entry.is_folder && entry.web_url && (
                      <a
                        className="text-[10px] font-medium text-accent-700 hover:underline"
                        href={entry.web_url}
                        onClick={event => event.stopPropagation()}
                        rel="noreferrer"
                        target="_blank"
                      >
                        View
                      </a>
                    )}
                    {!entry.is_folder && (
                      <a
                        className="text-[10px] font-medium text-accent-700 hover:underline"
                        href={api.downloadIntegrationFileUrl(connectionId, entry.id)}
                        onClick={event => event.stopPropagation()}
                      >
                        Download
                      </a>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {nextPageToken && (
          <button
            className="w-full border-t border-slate-100 py-1.5 text-[11px] font-medium text-accent-700 hover:bg-accent-50"
            disabled={state === 'loading'}
            onClick={loadMore}
            type="button"
          >
            {state === 'loading' ? 'Loading…' : 'Load more'}
          </button>
        )}
      </div>

      {multiple && (
        <div className="flex items-center justify-between border-t border-slate-200 bg-slate-50 px-2 py-1.5">
          <span className="text-[10px] text-ink-500">
            {checked.size === 0 ? 'Check items to add them' : `${checked.size} selected`}
          </span>
          <button
            className="rounded-md bg-accent-600 px-2 py-1 text-[10px] font-semibold text-white hover:bg-accent-700 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={checked.size === 0}
            onClick={confirmChecked}
            type="button"
          >
            Add {checked.size > 0 ? checked.size : ''} {mode === 'folder' ? 'folder(s)' : 'file(s)'}
          </button>
        </div>
      )}
    </div>
  );
}
