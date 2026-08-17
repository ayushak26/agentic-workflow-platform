import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type {
  ContractField,
  MCPOperationClass,
  MCPServerInfo,
  MCPToolInfo,
  MCPToolTestResult,
  OutputContract,
} from '../../../api/types';
import { FieldPicker } from './FieldPicker';
import { ValueTree } from './ExplanationView';

/**
 * Configuring a business-system capability.
 *
 *     Select Server → Select Tool → Map Inputs → Inspect Output
 *
 * Nothing about this component is Dynamics-specific. The server list, the tool
 * list, each tool's form fields and each tool's result shape are all discovered
 * from the MCP server at runtime. Pointing the same UI at SAP, Salesforce or an
 * internal ERP requires no change here — which is the reason MCP is the
 * extension mechanism rather than a node type per system.
 */

type Config = Record<string, unknown>;

const OPERATION_STYLES: Record<MCPOperationClass, string> = {
  read: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  write: 'border-amber-200 bg-amber-50 text-amber-800',
  destructive: 'border-red-200 bg-red-50 text-red-700',
  unknown: 'border-slate-200 bg-slate-50 text-ink-600',
  external_action: 'border-sky-200 bg-sky-50 text-sky-700',
};

const OPERATION_TITLES: Record<MCPOperationClass, string> = {
  read: 'Reads data. Changes nothing.',
  write: 'Changes data in the connected system.',
  destructive: 'Deletes or irreversibly changes data in the connected system.',
  unknown: 'Unclassified — treated as a write.',
  external_action: 'Triggers an action outside the platform — not a simple read or write.',
};

/** Shared safety-classification badge — one visual language for every node
 *  that touches something outside the platform, not just MCP tools. Used
 *  by MCPToolConfig (its original home), EmailConfig, ExternalActionConfig
 *  and SQLQueryConfig, so an author (and, on the canvas/Cockpit, a
 *  reader) sees the same badge regardless of which node type produced it. */
export function OperationBadge({ operation }: { operation: MCPOperationClass }) {
  return (
    <span
      className={`inline-flex rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
        OPERATION_STYLES[operation] ?? OPERATION_STYLES.unknown
      }`}
      title={OPERATION_TITLES[operation] ?? OPERATION_TITLES.unknown}
    >
      {operation}
    </span>
  );
}

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

type SchemaProperty = {
  type?: string | string[];
  description?: string;
  enum?: unknown[];
  default?: unknown;
  minimum?: number;
  maximum?: number;
};

function propertiesOf(schema: Record<string, unknown> | undefined): {
  properties: Record<string, SchemaProperty>;
  required: string[];
} {
  const properties = (schema?.properties ?? {}) as Record<string, SchemaProperty>;
  const required = Array.isArray(schema?.required)
    ? (schema.required as string[])
    : [];
  return { properties, required };
}

function primitiveType(property: SchemaProperty): string {
  const declared = Array.isArray(property.type)
    ? property.type.find(item => item !== 'null')
    : property.type;
  return typeof declared === 'string' ? declared : 'string';
}

export function MCPToolConfig({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const [servers, setServers] = useState<MCPServerInfo[]>([]);
  const [tools, setTools] = useState<MCPToolInfo[]>([]);
  const [loadingTools, setLoadingTools] = useState(false);
  const [discoveryError, setDiscoveryError] = useState<string | null>(null);
  const [query, setQuery] = useState('');

  const serverId = asString(config.server_id);
  const toolName = asString(config.tool);
  const argumentValues = (config.arguments as Record<string, unknown>) ?? {};

  useEffect(() => {
    api.mcpServers()
      .then(result => setServers(result.servers))
      .catch(() => setServers([]));
  }, []);

  const loadTools = useCallback(
    (id: string, refresh = false) => {
      if (!id) {
        setTools([]);
        return;
      }
      setLoadingTools(true);
      setDiscoveryError(null);
      api.mcpTools(id, refresh)
        .then(result => setTools(result.tools))
        .catch(error => {
          setTools([]);
          setDiscoveryError(
            error instanceof Error ? error.message : String(error),
          );
        })
        .finally(() => setLoadingTools(false));
    },
    [],
  );

  useEffect(() => { loadTools(serverId); }, [loadTools, serverId]);

  const server = servers.find(item => item.id === serverId);
  const tool = tools.find(item => item.name === toolName);

  const filteredTools = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return tools;
    return tools.filter(item =>
      `${item.name} ${item.title} ${item.description} ${item.typical_uses.join(' ')}`
        .toLowerCase()
        .includes(needle),
    );
  }, [query, tools]);

  const setArgument = (name: string, value: unknown) => {
    const next = { ...argumentValues };
    if (value === '' || value === undefined) delete next[name];
    else next[name] = value;
    onChange({ ...config, arguments: next });
  };

  return (
    <div>
      <section>
        <div className="builder-panel-heading">Server</div>
        <select
          className="builder-field mt-2"
          onChange={event => onChange({
            ...config,
            server_id: event.target.value,
            // Switching server invalidates the tool and its arguments: a tool
            // name from another system would silently fail at run time.
            tool: '',
            arguments: {},
          })}
          value={serverId}
        >
          <option value="">Choose a connected system…</option>
          {servers.map(item => (
            <option key={item.id} value={item.id}>
              {item.display_name}
              {item.is_mock ? ' — demo data' : ''}
              {item.running ? '' : ' (not connected)'}
            </option>
          ))}
        </select>

        {server && <ServerStatus onReconnect={() => loadTools(server.id, true)} server={server} />}
        {servers.length === 0 && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] leading-4 text-amber-900">
            No MCP server is configured in this deployment.
          </div>
        )}
      </section>

      {serverId && (
        <section className="mt-4">
          <div className="flex items-center justify-between">
            <div className="builder-panel-heading">Tool</div>
            <span className="text-[10px] text-ink-500">
              {loadingTools ? 'discovering…' : `${tools.length} available`}
            </span>
          </div>

          {discoveryError && (
            <div className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] leading-4 text-red-800">
              <div className="font-semibold">Could not reach this server</div>
              <div className="mt-0.5">{discoveryError}</div>
              <button
                className="mt-1 font-medium underline"
                onClick={() => loadTools(serverId, true)}
                type="button"
              >
                Try again
              </button>
            </div>
          )}

          {tools.length > 6 && (
            <input
              aria-label="Search tools"
              className="builder-field mt-2"
              onChange={event => setQuery(event.target.value)}
              placeholder="Search tools — customer, opportunity, order…"
              type="search"
              value={query}
            />
          )}

          <div className="mt-2 max-h-72 space-y-1 overflow-y-auto">
            {filteredTools.map(item => (
              <button
                className={`w-full rounded-md border p-2 text-left transition ${
                  item.name === toolName
                    ? 'border-accent-600 bg-accent-50'
                    : 'border-slate-200 hover:border-accent-400'
                }`}
                key={item.name}
                onClick={() => onChange({
                  ...config,
                  tool: item.name,
                  arguments: {},
                })}
                type="button"
              >
                <div className="flex items-center gap-1.5">
                  <span className="min-w-0 flex-1 truncate text-[11px] font-semibold text-ink-900">
                    {item.title}
                  </span>
                  <OperationBadge operation={item.operation} />
                </div>
                <div className="mt-0.5 line-clamp-2 text-[10px] leading-4 text-ink-500">
                  {item.description}
                </div>
              </button>
            ))}
            {!loadingTools && filteredTools.length === 0 && !discoveryError && (
              <div className="rounded-md border border-dashed border-slate-300 p-3 text-center text-[11px] text-ink-500">
                {query ? `No tool matches “${query}”.` : 'This server exposes no tools.'}
              </div>
            )}
          </div>
        </section>
      )}

      {tool && (
        <>
          <ToolInfo tool={tool} />
          <ToolForm
            argumentValues={argumentValues}
            contract={contract}
            onSetArgument={setArgument}
            tool={tool}
          />
          <ToolTester tool={tool} />

          {tool.operation !== 'read' && (
            <section className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-3">
              <div className="text-[11px] font-semibold text-amber-900">
                ⚠ This changes data in {tool.system}
              </div>
              <p className="mt-1 text-[11px] leading-4 text-amber-900">
                By default it will only run after a Human Review step has
                approved something on this path. That is the safe default;
                overriding it is a decision worth making deliberately.
              </p>
              <label className="mt-2 flex items-start gap-2 text-[11px] text-amber-900">
                <input
                  checked={Boolean(config.allow_unattended_write)}
                  className="mt-0.5"
                  onChange={event => onChange({
                    ...config,
                    allow_unattended_write: event.target.checked,
                  })}
                  type="checkbox"
                />
                <span>
                  Allow this write without a human review
                  <span className="block text-[10px] opacity-80">
                    The connection&apos;s own policy still applies — this cannot
                    grant permission the deployment has not given.
                  </span>
                </span>
              </label>
            </section>
          )}

          <label className="mt-4 flex items-start gap-2 text-[11px] text-ink-700">
            <input
              checked={config.fail_on_error !== false}
              className="mt-0.5"
              onChange={event => onChange({
                ...config,
                fail_on_error: event.target.checked,
              })}
              type="checkbox"
            />
            <span>
              Stop the run if this step fails
              <span className="block text-[10px] text-ink-500">
                Turn this off to let &ldquo;not found&rdquo; become a fact your
                rules can route on, instead of ending the run.
              </span>
            </span>
          </label>
        </>
      )}
    </div>
  );
}

function ServerStatus({
  onReconnect,
  server,
}: {
  onReconnect: () => void;
  server: MCPServerInfo;
}) {
  const healthy = server.status.healthy;
  return (
    <div className="mt-2 rounded-md border border-slate-200 bg-white p-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5">
          <span
            className={`h-2 w-2 rounded-full ${
              server.running && healthy !== false ? 'bg-emerald-500' : 'bg-amber-500'
            }`}
          />
          <span className="text-[11px] font-semibold text-ink-900">
            {server.running ? 'Connected' : 'Not connected'}
          </span>
          {/* Never let demo data pass for production. */}
          {server.is_mock && (
            <span className="rounded-full border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-[9px] font-semibold text-sky-700">
              demo data
            </span>
          )}
        </div>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={onReconnect}
          type="button"
        >
          Test connection
        </button>
      </div>

      <dl className="mt-1.5 space-y-0.5 text-[10px] text-ink-500">
        {server.environment_label && (
          <div>Environment: {server.environment_label}</div>
        )}
        <div>
          Writes:{' '}
          {server.write_policy === 'read_only'
            ? 'not permitted'
            : server.write_policy === 'require_approval'
              ? 'need a human review'
              : 'permitted unattended'}
        </div>
        {server.status.tool_count > 0 && (
          <div>{server.status.tool_count} tools available</div>
        )}
      </dl>

      {server.credentials.length > 0 && (
        <div className="mt-1.5 border-t border-slate-100 pt-1.5">
          <div className="text-[10px] font-semibold text-ink-600">Credentials</div>
          <ul className="mt-0.5 space-y-0.5">
            {server.credentials.map(item => (
              <li className="flex items-center gap-1 text-[10px]" key={item.variable}>
                <span className={item.configured ? 'text-emerald-600' : 'text-amber-600'}>
                  {item.configured ? '✓' : '○'}
                </span>
                <span className="font-mono text-ink-500">{item.variable}</span>
              </li>
            ))}
          </ul>
          <div className="mt-1 text-[10px] text-ink-400">
            Held by the deployment. Never part of a workflow.
          </div>
        </div>
      )}
    </div>
  );
}

/** §7: what this capability is, in business language. */
function ToolInfo({ tool }: { tool: MCPToolInfo }) {
  const { properties, required } = propertiesOf(tool.input_schema);
  return (
    <section className="mt-4 rounded-lg border border-slate-200 bg-white p-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold text-ink-900">{tool.title}</span>
        <OperationBadge operation={tool.operation} />
      </div>
      <p className="mt-1 text-[11px] leading-4 text-ink-600">{tool.description}</p>

      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
        <div>
          <dt className="font-semibold uppercase tracking-wide text-ink-500">System</dt>
          <dd className="text-ink-700">{tool.system}</dd>
        </div>
        <div>
          <dt className="font-semibold uppercase tracking-wide text-ink-500">
            External action
          </dt>
          <dd className="text-ink-700">{tool.external_action ? 'Yes' : 'No'}</dd>
        </div>
      </dl>

      <div className="mt-2 text-[10px]">
        <div className="font-semibold uppercase tracking-wide text-ink-500">Inputs</div>
        <ul className="mt-0.5 space-y-0.5">
          {Object.entries(properties).map(([name, property]) => (
            <li className="text-ink-700" key={name}>
              <span className="font-mono">{name}</span>
              <span className="ml-1 text-ink-500">
                {primitiveType(property)}
                {required.includes(name) ? ' · required' : ' · optional'}
              </span>
            </li>
          ))}
          {Object.keys(properties).length === 0 && (
            <li className="text-ink-500">No inputs.</li>
          )}
        </ul>
      </div>

      {tool.output_fields.length > 0 && (
        <div className="mt-2 text-[10px]">
          <div className="font-semibold uppercase tracking-wide text-ink-500">
            Outputs
          </div>
          <div className="mt-0.5 text-ink-600">
            {tool.output_fields.slice(0, 6).map(field => field.path).join(', ')}
            {tool.output_fields.length > 6 && ` and ${tool.output_fields.length - 6} more`}
          </div>
        </div>
      )}

      {tool.typical_uses.length > 0 && (
        <div className="mt-2 text-[10px]">
          <div className="font-semibold uppercase tracking-wide text-ink-500">
            Typical uses
          </div>
          <ul className="mt-0.5 list-inside list-disc text-ink-600">
            {tool.typical_uses.map(use => <li key={use}>{use}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}

/**
 * §6: the form is generated from the tool's declared input schema.
 *
 * Every field can be mapped from an upstream value or typed literally. The
 * author never constructs MCP JSON, and adding a tool to the server adds a form
 * here with no frontend change.
 */
function ToolForm({
  argumentValues,
  contract,
  onSetArgument,
  tool,
}: {
  argumentValues: Record<string, unknown>;
  contract: OutputContract | null;
  onSetArgument: (name: string, value: unknown) => void;
  tool: MCPToolInfo;
}) {
  const { properties, required } = propertiesOf(tool.input_schema);
  const [picking, setPicking] = useState<string | null>(null);

  if (Object.keys(properties).length === 0) return null;

  return (
    <section className="mt-4">
      <div className="builder-panel-heading">Inputs</div>
      <div className="mt-2 space-y-3">
        {Object.entries(properties).map(([name, property]) => {
          const value = argumentValues[name];
          const isRequired = required.includes(name);
          const kind = primitiveType(property);
          const label = name.replace(/_/g, ' ');
          const destinationKind = kind === 'string'
            ? 'text'
            : kind === 'integer' || kind === 'number'
              ? 'number'
              : kind === 'boolean'
                ? 'boolean'
                : 'any';
          return (
            <div key={name}>
              <div className="flex items-center justify-between">
                <label className="text-[11px] font-medium text-ink-700">
                  {label}
                  {isRequired && <span className="ml-1 text-red-500">*</span>}
                  <span className="ml-1 text-[10px] font-normal text-ink-500">
                    {kind}
                  </span>
                </label>
                <button
                  className="text-[11px] font-medium text-accent-700 hover:underline"
                  onClick={() => setPicking(picking === name ? null : name)}
                  type="button"
                >
                  {picking === name ? 'Close' : 'Map value'}
                </button>
              </div>
              {property.description && (
                <p className="text-[10px] leading-4 text-ink-500">
                  {property.description}
                </p>
              )}

              {Array.isArray(property.enum) && property.enum.length > 0 ? (
                <select
                  className="builder-field mt-1"
                  onChange={event => onSetArgument(name, event.target.value)}
                  value={String(value ?? '')}
                >
                  <option value="">Choose…</option>
                  {property.enum.map(option => (
                    <option key={String(option)} value={String(option)}>
                      {String(option)}
                    </option>
                  ))}
                </select>
              ) : kind === 'boolean' ? (
                <select
                  className="builder-field mt-1"
                  onChange={event => onSetArgument(
                    name,
                    event.target.value === '' ? undefined : event.target.value === 'true',
                  )}
                  value={value === undefined ? '' : String(Boolean(value))}
                >
                  <option value="">Not set</option>
                  <option value="true">true</option>
                  <option value="false">false</option>
                </select>
              ) : (
                <input
                  className="builder-field mt-1 font-mono"
                  onChange={event => {
                    const raw = event.target.value;
                    if ((kind === 'integer' || kind === 'number') && raw !== '' && !raw.includes('{{')) {
                      const parsed = Number(raw);
                      onSetArgument(name, Number.isNaN(parsed) ? raw : parsed);
                      return;
                    }
                    onSetArgument(name, raw);
                  }}
                  placeholder={
                    kind === 'integer' || kind === 'number'
                      ? String(property.default ?? '')
                      : '{{outputs.…}} or a literal value'
                  }
                  value={value === undefined || value === null ? '' : String(value)}
                />
              )}

              {picking === name && (
                <div className="mt-2 rounded border border-slate-200 p-2">
                  <FieldPicker
                    contract={contract}
                    destinationHint={property.description}
                    destinationKind={destinationKind}
                    destinationLabel={label}
                    onPick={(field: ContractField) => {
                      // Optional-reference form for anything that can legitimately
                      // be absent: a CRM lookup that found nothing has no id, and
                      // the step should skip rather than fail the run.
                      const reference = field.may_be_unavailable
                        ? field.reference.replace(/\}\}$/, '?}}')
                        : field.reference;
                      onSetArgument(name, reference);
                      setPicking(null);
                    }}
                    selectedReference={typeof value === 'string' ? value : undefined}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}

/** §21: run the tool right here while building. */
function ToolTester({ tool }: { tool: MCPToolInfo }) {
  const { properties, required } = propertiesOf(tool.input_schema);
  const [values, setValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<MCPToolTestResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  const run = () => {
    setBusy(true);
    setResult(null);
    const args: Record<string, unknown> = {};
    for (const [name, raw] of Object.entries(values)) {
      if (!raw.trim()) continue;
      const kind = primitiveType(properties[name] ?? {});
      args[name] = kind === 'integer' || kind === 'number' ? Number(raw) : raw;
    }
    api.mcpTestTool({ server_id: tool.server_id, tool: tool.name, arguments: args })
      .then(setResult)
      .catch(error => setResult({
        status: 'failed',
        server_id: tool.server_id,
        tool: tool.name,
        error: { message: error instanceof Error ? error.message : String(error) },
      }))
      .finally(() => setBusy(false));
  };

  if (tool.operation !== 'read') {
    return (
      <section className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-2 text-[10px] leading-4 text-ink-500">
        Write tools cannot be run from here. Testing one repeatedly while
        adjusting inputs would leave real records behind in {tool.system}.
      </section>
    );
  }

  return (
    <section className="mt-4">
      <button
        className="text-[11px] font-medium text-accent-700 hover:underline"
        onClick={() => setOpen(value => !value)}
        type="button"
      >
        {open ? 'Hide test' : 'Test this tool'}
      </button>

      {open && (
        <div className="mt-2 rounded-md border border-slate-200 p-2">
          <p className="text-[10px] leading-4 text-ink-500">
            Runs against {tool.system} with literal values — no mapping, no
            workflow.
          </p>
          <div className="mt-2 space-y-1.5">
            {Object.entries(properties).map(([name]) => (
              <label className="block text-[10px] font-medium text-ink-700" key={name}>
                {name}
                {required.includes(name) && <span className="ml-1 text-red-500">*</span>}
                <input
                  className="builder-field mt-0.5"
                  onChange={event => setValues(current => ({
                    ...current,
                    [name]: event.target.value,
                  }))}
                  value={values[name] ?? ''}
                />
              </label>
            ))}
          </div>
          <button
            className="ui-button ui-button--secondary mt-2 w-full justify-center"
            disabled={busy}
            onClick={run}
            type="button"
          >
            {busy ? 'Calling…' : 'Test tool'}
          </button>

          {result && (
            <div className="mt-2">
              {result.status === 'completed' ? (
                <div className="rounded-md border border-emerald-200 bg-emerald-50 p-2">
                  <div className="text-[11px] font-semibold text-emerald-800">
                    ✓ {tool.system} responded
                    {result.mode === 'mock' && (
                      <span className="ml-1 font-normal">(demo data)</span>
                    )}
                  </div>
                  <div className="mt-1 rounded bg-white p-2">
                    <ValueTree value={result.data} />
                  </div>
                </div>
              ) : (
                <div className="rounded-md border border-red-200 bg-red-50 p-2 text-[11px] leading-4 text-red-800">
                  <div className="font-semibold">
                    {result.error?.code ?? 'Failed'}
                  </div>
                  <div className="mt-0.5">{result.error?.message}</div>
                  {result.error?.suggested_action && (
                    <div className="mt-1 text-[10px]">
                      {result.error.suggested_action}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  );
}
