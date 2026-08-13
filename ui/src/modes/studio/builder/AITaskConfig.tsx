import { useState } from 'react';

import type {
  ContractField,
  FieldSpec,
  LLMModelInfo,
  NodePreset,
  OutputContract,
} from '../../../api/types';
import { FieldPicker } from './FieldPicker';
import { SchemaBuilder } from './SchemaBuilder';
import { ModelSelect } from '../ModelSelect';

/**
 * Configuring the one AI capability.
 *
 * Everything that would traditionally justify a new agent class lives on this
 * form: what the step does, how it should behave, what it must return, which
 * language it works in, and which model runs it. A German email extractor and
 * an English complaint classifier are two sets of values here — not two node
 * types.
 */

type Config = Record<string, unknown>;

const LANGUAGES = [
  { value: 'en', label: 'English' },
  { value: 'de', label: 'German' },
  { value: 'fr', label: 'French' },
  { value: 'nl', label: 'Dutch' },
  { value: 'es', label: 'Spanish' },
  { value: 'it', label: 'Italian' },
  { value: 'source', label: 'Same as the incoming content' },
];

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function AITaskConfig({
  config,
  contract,
  llmModels,
  presets,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  llmModels: LLMModelInfo[];
  presets: NodePreset[];
  onChange: (next: Config) => void;
}) {
  const [pickingInput, setPickingInput] = useState(false);
  const language = asRecord(config.language);
  const outputFields = (config.output_fields as FieldSpec[] | undefined) ?? [];
  const task = asString(config.task, 'extract');

  const set = (patch: Config) => onChange({ ...config, ...patch });

  const applyPreset = (preset: NodePreset) => {
    // A preset writes configuration and nothing else — it never selects a
    // different node type. The instruction is only replaced when the author
    // hasn't written one, so picking a preset late doesn't discard their work.
    const existing = asString(config.instruction).trim();
    set({
      task: preset.task ?? task,
      instruction: existing ? config.instruction : preset.instruction ?? '',
      include_confidence: preset.include_confidence ?? config.include_confidence ?? true,
      ...(preset.config ?? {}),
    });
  };

  return (
    <div>
      {presets.length > 0 && (
        <section>
          <div className="builder-panel-heading">What should this step do?</div>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {presets.map(preset => (
              <button
                className={`rounded-md border p-2 text-left transition ${
                  task === preset.task
                    ? 'border-accent-600 bg-accent-50'
                    : 'border-slate-200 hover:border-accent-400'
                }`}
                key={preset.id}
                onClick={() => applyPreset(preset)}
                type="button"
              >
                <div className="text-[11px] font-semibold text-ink-900">{preset.label}</div>
                <div className="mt-0.5 text-[10px] leading-4 text-ink-500">
                  {preset.summary}
                </div>
              </button>
            ))}
          </div>
        </section>
      )}

      <section className="mt-4">
        <label className="block text-[11px] font-medium text-ink-700">
          Instructions
          <textarea
            className="builder-field mt-1"
            onChange={event => set({ instruction: event.target.value })}
            placeholder={
              'Understand the incoming customer communication.\n\n'
              + 'Extract only information that is explicitly stated or strongly implied.\n'
              + 'Do not invent missing values.'
            }
            rows={6}
            value={asString(config.instruction)}
          />
        </label>
        <p className="mt-1 text-[11px] text-ink-500">
          Written for a colleague, not for a model. Say what to do and what not
          to guess at.
        </p>
      </section>

      <section className="mt-4">
        <div className="flex items-center justify-between">
          <label className="text-[11px] font-medium text-ink-700">
            Content this step reads
          </label>
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setPickingInput(value => !value)}
            type="button"
          >
            {pickingInput ? 'Close picker' : 'Pick a value'}
          </button>
        </div>
        <input
          className="builder-field mt-1 font-mono"
          onChange={event => set({ input: event.target.value })}
          placeholder="{{outputs.incoming_request.data.message}}"
          value={asString(config.input)}
        />
        {pickingInput && (
          <div className="mt-2 rounded border border-slate-200 p-2">
            <FieldPicker
              contract={contract}
              onPick={(field: ContractField) => {
                set({ input: field.reference });
                setPickingInput(false);
              }}
              selectedReference={asString(config.input)}
            />
          </div>
        )}
      </section>

      <SchemaBuilder
        fields={outputFields}
        onChange={fields => set({ output_fields: fields })}
      />

      <section className="mt-5 rounded-lg border border-slate-200 p-3">
        <div className="builder-panel-heading">Language</div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          Handled inside this one step. Detecting, reading and normalising in a
          single call is both cheaper and more accurate than translating first —
          translation is exactly what loses model designations and part numbers.
        </p>

        <label className="mt-3 block text-[11px] font-medium text-ink-700">
          Incoming language
          <select
            className="builder-field mt-1"
            onChange={event => set({
              language: { ...language, input_language: event.target.value },
            })}
            value={asString(language.input_language, 'auto')}
          >
            <option value="auto">Detect automatically</option>
            {LANGUAGES.filter(item => item.value !== 'source').map(item => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>

        <label className="mt-3 flex items-start gap-2 text-[11px] text-ink-700">
          <input
            checked={language.process_in_original_language !== false}
            className="mt-0.5"
            onChange={event => set({
              language: { ...language, process_in_original_language: event.target.checked },
            })}
            type="checkbox"
          />
          <span>
            Work in the original language
            <span className="block text-[10px] text-ink-500">
              Read the text as written instead of translating it first.
            </span>
          </span>
        </label>

        <label className="mt-3 block text-[11px] font-medium text-ink-700">
          Produce output in
          <select
            className="builder-field mt-1"
            onChange={event => set({
              language: { ...language, output_language: event.target.value },
            })}
            value={asString(language.output_language, 'en')}
          >
            {LANGUAGES.map(item => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </label>

        <label className="mt-3 flex items-start gap-2 text-[11px] text-ink-700">
          <input
            checked={language.preserve_original !== false}
            className="mt-0.5"
            onChange={event => set({
              language: { ...language, preserve_original: event.target.checked },
            })}
            type="checkbox"
          />
          <span>
            Keep quoted text verbatim
            <span className="block text-[10px] text-ink-500">
              Product names, model designations and serial numbers are never
              translated.
            </span>
          </span>
        </label>
      </section>

      <section className="mt-4 rounded-lg border border-slate-200 p-3">
        <div className="builder-panel-heading">Model</div>
        <ModelSelect
          className="mt-2"
          llmModels={llmModels}
          onChange={next => set({ model: next })}
          value={asString(config.model, 'auto')}
        />
        <p className="mt-1 text-[11px] text-ink-500">
          This step is provider-independent. Changing the model does not change
          what it returns — the output schema is the contract.
        </p>
      </section>

      <section className="mt-4 space-y-2">
        <label className="flex items-start gap-2 text-[11px] text-ink-700">
          <input
            checked={config.include_confidence !== false}
            className="mt-0.5"
            onChange={event => set({ include_confidence: event.target.checked })}
            type="checkbox"
          />
          <span>
            Ask for a confidence score
            <span className="block text-[10px] text-ink-500">
              Makes uncertainty routable: a Decision step can send anything below
              a threshold to a person.
            </span>
          </span>
        </label>

        <label className="flex items-start gap-2 text-[11px] text-ink-700">
          <input
            checked={config.fail_on_error !== false}
            className="mt-0.5"
            onChange={event => set({ fail_on_error: event.target.checked })}
            type="checkbox"
          />
          <span>
            Stop the run if this step fails
            <span className="block text-[10px] text-ink-500">
              Turn this off to let a refusal or an invalid response become a fact
              your rules can route on, instead of ending the run.
            </span>
          </span>
        </label>
      </section>
    </div>
  );
}
