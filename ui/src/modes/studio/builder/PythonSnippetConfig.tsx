import { useState } from 'react';

import { api } from '../../../api/client';
import type { FieldSpec, OutputContract } from '../../../api/types';
import { FieldPicker } from './FieldPicker';
import { SchemaBuilder } from './SchemaBuilder';

/**
 * Write-and-run a Python snippet. The code itself is plain text — no chip
 * mapping inline — because a snippet reads its inputs from a plain `inputs`
 * dict, not from `{{...}}` references embedded in the code (which, unlike
 * every other text field in the Builder, would be a real SQL/code-injection
 * shape if resolved that way — see app/nodes/python_snippet.py). Instead,
 * each named input below is mapped once, the same way an MCP tool's
 * arguments are.
 */

type Config = Record<string, unknown>;

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

export function PythonSnippetConfig({
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

  const inputFields = (config.input_fields as Record<string, unknown>) ?? {};
  const outputFields = (config.output_fields as FieldSpec[]) ?? [];
  const entries = Object.entries(inputFields);

  const setInput = (name: string, value: unknown) => {
    const next = { ...inputFields };
    if (name) next[name] = value;
    onChange({ ...config, input_fields: next });
  };
  const renameInput = (oldName: string, newName: string) => {
    if (!newName || newName === oldName) return;
    const next = { ...inputFields };
    const value = next[oldName];
    delete next[oldName];
    next[newName] = value;
    onChange({ ...config, input_fields: next });
  };
  const removeInput = (name: string) => {
    const next = { ...inputFields };
    delete next[name];
    onChange({ ...config, input_fields: next });
  };

  const generate = async () => {
    setBusy(true);
    setGenError(null);
    try {
      const result = await api.draftCode({
        language: 'python',
        existing_code: asString(config.code),
        instructions,
        input_fields: Object.keys(inputFields).map(name => ({ name })),
        output_fields: outputFields.map(f => ({ name: f.name, type: f.type, description: f.description })),
      });
      onChange({ ...config, code: result.answer });
      setGenerating(false);
    } catch (error) {
      setGenError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <section>
        <div className="flex items-center justify-between">
          <div className="builder-panel-heading">Inputs</div>
        </div>
        <p className="mt-1 text-[10px] leading-4 text-ink-500">
          Available inside the code as <code>inputs[&quot;name&quot;]</code>.
        </p>
        <div className="mt-2 space-y-2">
          {entries.map(([name, value]) => (
            <div key={name}>
              <div className="flex items-center gap-1.5">
                <input
                  className="builder-field font-mono"
                  onChange={event => renameInput(name, event.target.value)}
                  placeholder="name"
                  value={name}
                />
                <input
                  className="builder-field font-mono"
                  onChange={event => setInput(name, event.target.value)}
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
                  aria-label={`Remove input ${name}`}
                  className="ui-button ui-button--secondary px-2"
                  onClick={() => removeInput(name)}
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
                    onPick={field => { setInput(name, field.reference); setPicking(null); }}
                  />
                </div>
              )}
            </div>
          ))}
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setInput(`input_${entries.length + 1}`, '')}
            type="button"
          >
            + Add input
          </button>
        </div>
      </section>

      <section className="mt-4">
        <div className="flex items-center justify-between">
          <div className="builder-panel-heading">Code (Python)</div>
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setGenerating(v => !v)}
            type="button"
          >
            {generating ? 'Close' : '✨ Generate Code'}
          </button>
        </div>

        {generating && (
          <div className="mt-2 rounded border border-slate-200 p-2">
            <textarea
              className="builder-field"
              onChange={event => setInstructions(event.target.value)}
              placeholder="Describe what this snippet should do…"
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
          onChange={event => onChange({ ...config, code: event.target.value })}
          placeholder={'output["result"] = inputs.get("a", 0) + 1'}
          rows={10}
          spellCheck={false}
          value={asString(config.code)}
        />
      </section>

      <section className="mt-4">
        <div className="builder-panel-heading">Outputs</div>
        <p className="mt-1 text-[10px] leading-4 text-ink-500">
          Assign these fields to <code>output</code> in the code above, e.g. <code>output[&quot;total&quot;] = ...</code>.
        </p>
        <SchemaBuilder
          fields={outputFields}
          onChange={fields => onChange({ ...config, output_fields: fields })}
          sampleContent={asString(config.code)}
        />
      </section>

      <section className="mt-4 grid grid-cols-3 gap-2">
        <label className="text-[11px] font-medium text-ink-700">
          Timeout (s)
          <input
            className="builder-field mt-1"
            max={60}
            min={1}
            onChange={event => onChange({ ...config, timeout_seconds: Number(event.target.value) })}
            type="number"
            value={typeof config.timeout_seconds === 'number' ? config.timeout_seconds : 10}
          />
        </label>
        <label className="text-[11px] font-medium text-ink-700">
          Memory (MB)
          <input
            className="builder-field mt-1"
            max={1024}
            min={16}
            onChange={event => onChange({ ...config, memory_mb: Number(event.target.value) })}
            type="number"
            value={typeof config.memory_mb === 'number' ? config.memory_mb : 128}
          />
        </label>
        <label className="text-[11px] font-medium text-ink-700">
          Max output (bytes)
          <input
            className="builder-field mt-1"
            max={2_000_000}
            min={1000}
            onChange={event => onChange({ ...config, max_output_bytes: Number(event.target.value) })}
            type="number"
            value={typeof config.max_output_bytes === 'number' ? config.max_output_bytes : 200_000}
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
            Turn this off to let a snippet failure become a routable status instead.
          </span>
        </span>
      </label>
    </div>
  );
}
