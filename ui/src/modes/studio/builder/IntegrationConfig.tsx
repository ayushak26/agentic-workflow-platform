import { useEffect, useRef, useState } from 'react';

import { api } from '../../../api/client';
import type {
  ContractField,
  IntegrationConnectionInfo,
  NodePreset,
  OutputContract,
} from '../../../api/types';
import { CloudFileBrowser } from './CloudFileBrowser';
import { FieldPicker } from './FieldPicker';

/**
 * The Integration capability's configuration.
 *
 * One provider (chosen once, from a preset, at add-time-equivalent — see the
 * "Choose provider" section below) plus one connection plus one operation.
 * Every operation here is read-only: browsing and downloading a file never
 * changes anything in the connected account.
 *
 * select_file/get_file/select_folder can target one id or several — the
 * config field holds either a plain string (a literal id or a `{{...}}`
 * template wired from an upstream step) or a string array (one or more ids
 * picked from the file browser). list_folder/search_files's folder scope
 * stays single — you list or search exactly one folder at a time.
 */

type Config = Record<string, unknown>;
type Provider = 'google_drive' | 'onedrive';
type Picked = { id: string; name: string };

const PROVIDER_LABELS: Record<Provider, string> = {
  google_drive: 'Google Drive',
  onedrive: 'OneDrive',
};

const OPERATIONS: Array<{ id: string; label: string; summary: string }> = [
  { id: 'list_folder', label: 'List Folder', summary: 'List the contents of a folder.' },
  { id: 'search_files', label: 'Search Files', summary: 'Search this account by name.' },
  { id: 'select_file', label: 'Select File', summary: 'Pick one or more files and expose their metadata.' },
  { id: 'select_folder', label: 'Select Folder', summary: 'Pick one or more folders and expose their metadata.' },
  { id: 'get_file', label: 'Get File', summary: 'Download one or more files’ content into the workflow.' },
];

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function isTemplateRef(value: unknown): boolean {
  return typeof value === 'string' && value.trim().startsWith('{{');
}

/** A config value that isn't a template reference, normalized to a list of
 *  ids — 0, 1, or many, regardless of whether it was stored as a bare
 *  string or an array. */
function toIdList(value: unknown): string[] {
  if (Array.isArray(value)) return value.filter((item): item is string => typeof item === 'string');
  if (typeof value === 'string' && value && !isTemplateRef(value)) return [value];
  return [];
}

/** Opens the provider's consent screen in a popup and calls `onComplete`
 *  once the callback page (app/api/integration_oauth.py) posts back. */
function useIntegrationOAuthPopup(onComplete: () => void) {
  const onCompleteRef = useRef(onComplete);
  // Latest-ref capture happens in an effect (not during render) so the ref
  // write stays out of the render phase; the message handler below reads it
  // asynchronously, so the observable timing is unchanged.
  useEffect(() => {
    onCompleteRef.current = onComplete;
  });

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      if (event.data?.type === 'integration-oauth-complete') onCompleteRef.current();
    }
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, []);

  return (provider: Provider) => {
    window.open(
      api.integrationConnectUrl(provider),
      'integration-oauth-connect',
      'width=520,height=680,noopener=no',
    );
  };
}

function ConnectionManagement({
  connection,
  onConnectionsChanged,
}: {
  connection: IntegrationConnectionInfo;
  onConnectionsChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const disconnect = () => {
    if (!window.confirm(`Disconnect ${connection.display_name}? Workflows using this connection will stop working until it's reconnected.`)) return;
    setBusy(true);
    setError(null);
    api.disconnectIntegrationConnection(connection.id)
      .then(onConnectionsChanged)
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  };

  return (
    <div className="mt-2 flex items-center justify-between gap-2 rounded-md border border-slate-200 bg-slate-50 p-2">
      <span className="text-[11px] text-ink-700">
        {connection.needs_reauth ? (
          <span className="font-medium text-amber-700">Reauthentication required</span>
        ) : (
          'Connected'
        )}
      </span>
      <button
        className="text-[11px] font-medium text-bad hover:underline disabled:opacity-50"
        disabled={busy}
        onClick={disconnect}
        type="button"
      >
        {busy ? 'Disconnecting…' : 'Disconnect'}
      </button>
      {error && <p className="text-[10px] text-bad">{error}</p>}
    </div>
  );
}

function StaticOrPickedField({
  contract,
  label,
  onChange,
  onOpenBrowser,
  placeholder,
  value,
}: {
  contract: OutputContract | null;
  label: string;
  onChange: (next: string) => void;
  onOpenBrowser?: () => void;
  placeholder: string;
  value: string;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <div className="mt-2">
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-ink-700">{label}</label>
        <div className="flex gap-2">
          {onOpenBrowser && (
            <button
              className="text-[11px] font-medium text-accent-700 hover:underline"
              onClick={onOpenBrowser}
              type="button"
            >
              Browse…
            </button>
          )}
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setPicking(value => !value)}
            type="button"
          >
            {picking ? 'Close picker' : 'Pick a value'}
          </button>
        </div>
      </div>
      <input
        className="builder-field mt-1 font-mono"
        onChange={event => onChange(event.target.value)}
        placeholder={placeholder}
        value={value}
      />
      {picking && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <FieldPicker
            contract={contract}
            destinationKind="text"
            onPick={(field: ContractField) => {
              onChange(field.reference);
              setPicking(false);
            }}
            selectedReference={value}
          />
        </div>
      )}
    </div>
  );
}

/** file_id/folder_id for select_file, get_file, select_folder — one or more
 *  ids as removable chips, "Add" opens the browser in multi-select mode, or
 *  fall back to a plain templated string for wiring from an upstream step. */
function MultiPickerField({
  browseMode,
  connection,
  contract,
  label,
  onChange,
  provider,
  value,
}: {
  browseMode: 'file' | 'folder';
  connection: IntegrationConnectionInfo | undefined;
  contract: OutputContract | null;
  label: string;
  onChange: (next: string | string[] | null) => void;
  provider: Provider;
  value: unknown;
}) {
  const [names, setNames] = useState<Record<string, string>>({});
  const [browsing, setBrowsing] = useState(false);
  const [templateMode, setTemplateMode] = useState(isTemplateRef(value));
  const ids = toIdList(value);

  const addPicked = (files: Picked[]) => {
    setNames(previous => {
      const next = { ...previous };
      for (const file of files) next[file.id] = file.name;
      return next;
    });
    const merged = [...new Set([...ids, ...files.map(file => file.id)])];
    onChange(merged.length === 1 ? merged[0] : merged);
    setBrowsing(false);
  };

  const remove = (id: string) => {
    const next = ids.filter(item => item !== id);
    onChange(next.length === 0 ? null : next.length === 1 ? next[0] : next);
  };

  if (templateMode) {
    return (
      <div className="mt-2">
        <div className="flex items-center justify-between">
          <label className="text-[11px] font-medium text-ink-700">{label}</label>
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setTemplateMode(false)}
            type="button"
          >
            Choose from {PROVIDER_LABELS[provider]} instead
          </button>
        </div>
        <StaticOrPickedField
          contract={contract}
          label=""
          onChange={onChange}
          placeholder="{{outputs.previous_step.first.id}}"
          value={typeof value === 'string' ? value : ''}
        />
      </div>
    );
  }

  return (
    <div className="mt-2">
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-ink-700">{label}</label>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setTemplateMode(true)}
          type="button"
        >
          Use a template value instead
        </button>
      </div>
      {ids.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {ids.map(id => (
            <span
              className="inline-flex items-center gap-1 rounded-full border border-slate-200 bg-slate-50 py-0.5 pl-2 pr-1 text-[10px] text-ink-700"
              key={id}
            >
              {names[id] ?? id}
              <button
                aria-label={`Remove ${names[id] ?? id}`}
                className="text-ink-400 hover:text-red-600"
                onClick={() => remove(id)}
                type="button"
              >×</button>
            </span>
          ))}
        </div>
      )}
      <button
        className="mt-1.5 rounded-md border border-dashed border-slate-300 px-2 py-1 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50 disabled:cursor-not-allowed disabled:opacity-50"
        disabled={!connection}
        onClick={() => setBrowsing(value => !value)}
        type="button"
      >
        {browsing ? 'Close browser' : `+ Add ${browseMode === 'file' ? 'files' : 'folders'}`}
      </button>
      {!connection && (
        <p className="mt-1 text-[10px] text-ink-500">Choose a connected account above before browsing.</p>
      )}
      {browsing && connection && (
        <CloudFileBrowser
          connectionId={connection.id}
          mode={browseMode}
          multiple
          onSelect={addPicked}
        />
      )}
    </div>
  );
}

export function IntegrationConfig({
  config,
  connections,
  contract,
  onChange,
  onConnectionsChanged,
  presets,
}: {
  config: Config;
  connections: IntegrationConnectionInfo[];
  contract: OutputContract | null;
  onChange: (next: Config) => void;
  onConnectionsChanged: () => void;
  presets: NodePreset[];
}) {
  const provider = (config.provider as Provider | undefined) || undefined;
  const operation = asString(config.operation, 'list_folder');
  const connectionId = asString(config.connection);
  const set = (patch: Config) => onChange({ ...config, ...patch });
  const openConnectPopup = useIntegrationOAuthPopup(onConnectionsChanged);
  const [browsingScope, setBrowsingScope] = useState(false);

  if (!provider) {
    return (
      <section>
        <div className="builder-panel-heading">Choose provider</div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {presets.map(preset => (
            <button
              className="rounded-md border border-slate-200 p-2 text-left transition hover:border-accent-400"
              key={preset.id}
              onClick={() => set({ ...(preset.config ?? {}) })}
              type="button"
            >
              <div className="text-[11px] font-semibold text-ink-900">{preset.label}</div>
              <div className="mt-0.5 text-[10px] leading-4 text-ink-500">{preset.summary}</div>
            </button>
          ))}
        </div>
      </section>
    );
  }

  const providerConnections = connections.filter(item => item.provider === provider);
  const connection = providerConnections.find(item => item.id === connectionId);

  return (
    <div>
      <section>
        <div className="builder-panel-heading">{PROVIDER_LABELS[provider]} connection</div>
        {providerConnections.length === 0 ? (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] leading-4 text-amber-900">
            No {PROVIDER_LABELS[provider]} account is connected for this deployment.
            Connect one below, or a workflow can still be built — preflight will
            block the run until a connection exists.
          </div>
        ) : (
          <select
            className="builder-field mt-2"
            onChange={event => set({ connection: event.target.value })}
            value={connectionId}
          >
            <option value="">Choose an account…</option>
            {providerConnections.map(item => (
              <option key={item.id} value={item.id}>
                {item.display_name}
                {item.needs_reauth ? ' (reauthentication required)' : ''}
              </option>
            ))}
          </select>
        )}
        <button
          className="mt-2 w-full rounded-md border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
          onClick={() => openConnectPopup(provider)}
          type="button"
        >
          + Connect {PROVIDER_LABELS[provider]}
        </button>
        {connection && (
          <ConnectionManagement connection={connection} onConnectionsChanged={onConnectionsChanged} />
        )}
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">Operation</div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          {OPERATIONS.map(item => (
            <button
              className={`rounded-md border p-2 text-left transition ${
                operation === item.id ? 'border-accent-600 bg-accent-50' : 'border-slate-200 hover:border-accent-400'
              }`}
              key={item.id}
              onClick={() => set({ operation: item.id })}
              type="button"
            >
              <div className="text-[11px] font-semibold text-ink-900">{item.label}</div>
              <div className="mt-0.5 text-[10px] leading-4 text-ink-500">{item.summary}</div>
            </button>
          ))}
        </div>
      </section>

      {operation === 'search_files' && (
        <section className="mt-4">
          <div className="builder-panel-heading">Search</div>
          <StaticOrPickedField
            contract={contract}
            label="Query"
            onChange={text => set({ query: text })}
            placeholder="{{outputs.previous_step.first.name}}"
            value={asString(config.query)}
          />
          <StaticOrPickedField
            contract={contract}
            label="Folder to search within (optional)"
            onChange={text => set({ folder_id: text })}
            onOpenBrowser={() => setBrowsingScope(true)}
            placeholder="Root when empty"
            value={asString(config.folder_id)}
          />
        </section>
      )}

      {operation === 'list_folder' && (
        <section className="mt-4">
          <StaticOrPickedField
            contract={contract}
            label="Folder to list"
            onChange={text => set({ folder_id: text })}
            onOpenBrowser={() => setBrowsingScope(true)}
            placeholder="Root when empty"
            value={asString(config.folder_id)}
          />
        </section>
      )}

      {(operation === 'list_folder' || operation === 'search_files') && browsingScope && (
        <section className="mt-2">
          <div className="flex items-center justify-between">
            <div className="builder-panel-heading">Browse {PROVIDER_LABELS[provider]}</div>
            <button
              className="text-[11px] font-medium text-ink-500 hover:underline"
              onClick={() => setBrowsingScope(false)}
              type="button"
            >
              Close
            </button>
          </div>
          {connection ? (
            <CloudFileBrowser
              connectionId={connection.id}
              mode="folder"
              onSelect={files => {
                set({ folder_id: files[0]?.id ?? '' });
                setBrowsingScope(false);
              }}
            />
          ) : (
            <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] leading-4 text-amber-900">
              Choose a connected account above before browsing.
            </div>
          )}
        </section>
      )}

      {operation === 'select_folder' && (
        <section className="mt-4">
          <MultiPickerField
            browseMode="folder"
            connection={connection}
            contract={contract}
            label="Folder(s)"
            onChange={next => set({ folder_id: next })}
            provider={provider}
            value={config.folder_id}
          />
        </section>
      )}

      {(operation === 'select_file' || operation === 'get_file') && (
        <section className="mt-4">
          <MultiPickerField
            browseMode="file"
            connection={connection}
            contract={contract}
            label="File(s)"
            onChange={next => set({ file_id: next })}
            provider={provider}
            value={config.file_id}
          />
        </section>
      )}
    </div>
  );
}
