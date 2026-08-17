import { useState } from 'react';

import { api } from '../../../api/client';
import type { OutputContract } from '../../../api/types';
import { FieldPicker } from './FieldPicker';
import { OperationBadge } from './MCPToolConfig';

/**
 * A read-only SQL lookup — the escape hatch for a query the classified
 * business-records tools (Customer Search, Order Search, ...) don't already
 * cover. Deliberately read-only in the UI too: there is no write mode to
 * configure, because there is no write mode in the tool this calls
 * (app/mcp/business_records/sql_guard.py).
 */

type Config = Record<string, unknown>;

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

export function SQLQueryConfig({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const [picking, setPicking] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [instructions, setInstructions] = useState('');
  const [busy, setBusy] = useState(false);
  const [genError, setGenError] = useState<string | null>(null);

  const params = (config.params as Record<string, unknown>) ?? {};
  const entries = Object.entries(params);

  const setParam = (name: string, value: unknown) => {
    const next = { ...params };
    if (name) next[name] = value;
    onChange({ ...config, params: next });
  };
  const renameParam = (oldName: string, newName: string) => {
    if (!newName || newName === oldName) return;
    const next = { ...params };
    const value = next[oldName];
    delete next[oldName];
    next[newName] = value;
    onChange({ ...config, params: next });
  };
  const removeParam = (name: string) => {
    const next = { ...params };
    delete next[name];
    onChange({ ...config, params: next });
  };

  const generate = async () => {
    setBusy(true);
    setGenError(null);
    try {
      const result = await api.draftCode({
        language: 'sql',
        existing_code: asString(config.sql),
        instructions,
        input_fields: Object.keys(params).map(name => ({ name })),
      });
      onChange({ ...config, sql: result.answer });
      setGenerating(false);
    } catch (error) {
      setGenError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <section className="flex items-center gap-2">
        <OperationBadge operation="read" />
        <span className="text-[10px] text-ink-500">
          Read-only, always — see the connection&apos;s own defense-in-depth.
        </span>
      </section>

      <section className="mt-4">
        <div className="flex items-center justify-between">
          <div className="builder-panel-heading">SQL</div>
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setGenerating(v => !v)}
            type="button"
          >
            {generating ? 'Close' : '✨ Generate Code'}
          </button>
        </div>
        <p className="mt-1 text-[10px] leading-4 text-ink-500">
          A single SELECT statement. Use %(name)s placeholders — never map a
          value directly into this text.
        </p>

        {generating && (
          <div className="mt-2 rounded border border-slate-200 p-2">
            <textarea
              className="builder-field"
              onChange={event => setInstructions(event.target.value)}
              placeholder="Describe what this query should find…"
              rows={2}
              value={instructions}
            />
            {genError && <div className="mt-1 text-[11px] text-bad">{genError}</div>}
            <button
              className="ui-button ui-button--secondary mt-2 w-full justify-center"
              disabled={busy}
              onClick={generate}
              type="button"
            >
              {busy ? 'Generating…' : 'Generate'}
            </button>
          </div>
        )}

        <textarea
          className="builder-field mt-2 font-mono"
          onChange={event => onChange({ ...config, sql: event.target.value })}
          placeholder={'SELECT name FROM crm_accounts WHERE name LIKE %(pattern)s'}
          rows={6}
          spellCheck={false}
          value={asString(config.sql)}
        />
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">Params</div>
        <p className="mt-1 text-[10px] leading-4 text-ink-500">
          Named values for the query&apos;s %(name)s placeholders.
        </p>
        <div className="mt-2 space-y-2">
          {entries.map(([name, value]) => (
            <div key={name}>
              <div className="flex items-center gap-1.5">
                <input
                  className="builder-field font-mono"
                  onChange={event => renameParam(name, event.target.value)}
                  placeholder="name"
                  value={name}
                />
                <input
                  className="builder-field font-mono"
                  onChange={event => setParam(name, event.target.value)}
                  placeholder="{{outputs.…}} or a literal value"
                  value={typeof value === 'string' ? value : JSON.stringify(value)}
                />
                <button
                  className="text-[11px] font-medium text-accent-700 hover:underline"
                  onClick={() => setPicking(picking === name ? null : name)}
                  type="button"
                >
                  {picking === name ? 'Close' : 'Map'}
                </button>
                <button
                  aria-label={`Remove param ${name}`}
                  className="ui-button ui-button--secondary px-2"
                  onClick={() => removeParam(name)}
                  type="button"
                >
                  ×
                </button>
              </div>
              {picking === name && (
                <div className="mt-1 rounded border border-slate-200 p-2">
                  <FieldPicker
                    contract={contract}
                    destinationKind="any"
                    destinationLabel={name}
                    onPick={field => { setParam(name, field.reference); setPicking(null); }}
                  />
                </div>
              )}
            </div>
          ))}
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setParam(`param_${entries.length + 1}`, '')}
            type="button"
          >
            + Add param
          </button>
        </div>
      </section>

      <section className="mt-4 grid grid-cols-2 gap-2">
        <label className="text-[11px] font-medium text-ink-700">
          Max rows
          <input
            className="builder-field mt-1"
            max={500}
            min={1}
            onChange={event => onChange({ ...config, max_rows: Number(event.target.value) })}
            type="number"
            value={typeof config.max_rows === 'number' ? config.max_rows : 100}
          />
        </label>
        <label className="text-[11px] font-medium text-ink-700">
          Timeout (s)
          <input
            className="builder-field mt-1"
            max={30}
            min={1}
            onChange={event => onChange({ ...config, timeout_seconds: Number(event.target.value) })}
            type="number"
            value={typeof config.timeout_seconds === 'number' ? config.timeout_seconds : 10}
          />
        </label>
      </section>

      <label className="mt-4 flex items-start gap-2 text-[11px] text-ink-700">
        <input
          checked={config.fail_on_error !== false}
          className="mt-0.5"
          onChange={event => onChange({ ...config, fail_on_error: event.target.checked })}
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
    </div>
  );
}
