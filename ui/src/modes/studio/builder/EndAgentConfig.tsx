import { useState } from 'react';

import type { ContractField, OutputContract } from '../../../api/types';
import { ModeCard } from './RouterEditor';
import { ValuePicker } from './FieldPicker';

/**
 * EndAgent's config editor — what this workflow returns or shows.
 *
 * Every mapped value is a `{{...}}` reference picked via `ValuePicker` — the
 * exact same mapping component used everywhere else in the Builder (the RAG
 * Agent node's Query field, the generic Inputs tab) — never a typed
 * expression a non-technical author has to write by hand.
 */

type Config = Record<string, unknown>;
type EndOutputField = { key: string; value_from: string };

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

export function EndAgentConfig({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const mode = asString(config.mode, 'workflow_result');
  const set = (patch: Config) => onChange({ ...config, ...patch });

  return (
    <div>
      <section className="mb-3 grid grid-cols-3 gap-1.5">
        <ModeCard
          active={mode === 'workflow_result'}
          description="Return mapped workflow outputs as structured data."
          label="Workflow Result"
          onSelect={() => set({ mode: 'workflow_result' })}
        />
        <ModeCard
          active={mode === 'chat_response'}
          description="Reply to (or route) a chatbot conversation."
          label="Chat Response"
          onSelect={() => set({ mode: 'chat_response' })}
        />
        <ModeCard
          active={mode === 'custom_response'}
          description="A human-friendly title and message."
          label="Custom Response"
          onSelect={() => set({ mode: 'custom_response' })}
        />
      </section>

      {mode === 'workflow_result' && (
        <OutputMappingList config={config} contract={contract} onChange={onChange} />
      )}
      {mode === 'custom_response' && (
        <CustomResponseFields config={config} onChange={onChange} />
      )}
      {mode === 'chat_response' && (
        <ChatResponseFields config={config} contract={contract} onChange={onChange} />
      )}
    </div>
  );
}

function MappedValueField({
  label,
  hint,
  value,
  contract,
  onChange,
  optional = false,
}: {
  label: string;
  hint?: string;
  value: string;
  contract: OutputContract | null;
  onChange: (next: string) => void;
  optional?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-ink-700">
          {label} {optional && <span className="font-normal text-ink-400">Optional</span>}
        </label>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setPicking(v => !v)}
          type="button"
        >
          {picking ? 'Close picker' : 'Pick a value'}
        </button>
      </div>
      <input
        className="builder-field mt-1 font-mono"
        onChange={event => onChange(event.target.value)}
        placeholder="{{outputs.previous_step.output}}"
        value={value}
      />
      {hint && <p className="mt-0.5 text-[10px] text-ink-500">{hint}</p>}
      {picking && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <ValuePicker
            contract={contract}
            destinationKind="any"
            destinationLabel={label}
            onPick={(field: ContractField) => { onChange(field.reference); setPicking(false); }}
            selectedReference={value || undefined}
          />
        </div>
      )}
    </div>
  );
}

function OutputMappingList({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const outputs = (config.outputs as EndOutputField[] | undefined) ?? [];
  const setOutputs = (next: EndOutputField[]) => onChange({ ...config, outputs: next });
  const replace = (index: number, patch: Partial<EndOutputField>) => {
    const copy = [...outputs];
    copy[index] = { ...copy[index], ...patch };
    setOutputs(copy);
  };

  return (
    <section>
      <div className="builder-panel-heading">Final Output</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        What this workflow returns. Any type of value from any earlier step —
        text, a number, a list, a whole object.
      </p>
      <div className="mt-2 space-y-2">
        {outputs.map((item, index) => (
          <div className="rounded-md border border-slate-200 p-2" key={index}>
            <div className="flex items-center gap-2">
              <input
                aria-label="Output name"
                className="builder-field flex-1 font-mono"
                onChange={event => replace(index, { key: event.target.value })}
                placeholder="answer"
                value={item.key}
              />
              <button
                aria-label={`Remove ${item.key || 'output'}`}
                className="px-1 text-ink-400 hover:text-red-600"
                onClick={() => setOutputs(outputs.filter((_, position) => position !== index))}
                type="button"
              >×</button>
            </div>
            <div className="mt-1.5">
              <MappedValueField
                contract={contract}
                label="Value"
                onChange={next => replace(index, { value_from: next })}
                value={item.value_from ?? ''}
              />
            </div>
          </div>
        ))}
        <button
          className="w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
          onClick={() => setOutputs([...outputs, { key: '', value_from: '' }])}
          type="button"
        >
          + Add Output
        </button>
      </div>
    </section>
  );
}

function CustomResponseFields({ config, onChange }: { config: Config; onChange: (next: Config) => void }) {
  return (
    <div className="space-y-3">
      <label className="block text-[11px] font-medium text-ink-700">
        Title
        <input
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, title: event.target.value })}
          placeholder="Request Completed"
          value={asString(config.title)}
        />
      </label>
      <label className="block text-[11px] font-medium text-ink-700">
        Message
        <textarea
          className="builder-field mt-1"
          onChange={event => onChange({ ...config, message: event.target.value })}
          placeholder="{{answer}}"
          rows={3}
          value={asString(config.message)}
        />
      </label>
    </div>
  );
}

function ChatResponseFields({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const outcome = asString(config.outcome, 'reply');
  const handoff = (config.handoff as Record<string, string> | undefined) ?? {};

  const setHandoff = (next: Record<string, string>) => onChange({ ...config, handoff: next });

  return (
    <div className="space-y-3">
      <MappedValueField
        contract={contract}
        label="Message"
        onChange={next => onChange({ ...config, chat_message: next })}
        value={asString(config.chat_message)}
      />

      <div>
        <div className="text-[11px] font-medium text-ink-700">Outcome</div>
        <div className="mt-1 grid grid-cols-2 gap-1.5">
          <ModeCard active={outcome === 'reply'} description="Respond to the user." label="Reply" onSelect={() => onChange({ ...config, outcome: 'reply' })} />
          <ModeCard active={outcome === 'route'} description="Respond, and hand the case to a department." label="Route" onSelect={() => onChange({ ...config, outcome: 'route' })} />
        </div>
      </div>

      {outcome === 'route' && (
        <>
          <label className="block text-[11px] font-medium text-ink-700">
            Department (stable value)
            <input
              className="builder-field mt-1 font-mono"
              onChange={event => onChange({ ...config, route_to: event.target.value })}
              placeholder="customer_support"
              value={asString(config.route_to)}
            />
          </label>
          <label className="block text-[11px] font-medium text-ink-700">
            Department label
            <span className="ml-1 font-normal text-ink-400">Optional — humanized from the value above if left blank</span>
            <input
              className="builder-field mt-1"
              onChange={event => onChange({ ...config, route_to_label: event.target.value })}
              placeholder="Customer Support"
              value={asString(config.route_to_label)}
            />
          </label>
        </>
      )}

      <MappedValueField
        contract={contract}
        hint="Usually a RAG Agent's sources output."
        label="Sources"
        onChange={next => onChange({ ...config, sources: next })}
        optional
        value={asString(config.sources)}
      />

      <section>
        <div className="builder-panel-heading">
          Handoff data <span className="font-normal text-ink-400">Optional</span>
        </div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          Business information the receiving team needs — never shown to the customer.
        </p>
        <div className="mt-2 space-y-2">
          {Object.entries(handoff).map(([key, value], index) => (
            <div className="flex items-center gap-2" key={index}>
              <input
                aria-label="Field name"
                className="builder-field w-1/3 font-mono"
                onChange={event => {
                  const next: Record<string, string> = {};
                  Object.entries(handoff).forEach(([k, v]) => { next[k === key ? event.target.value : k] = v; });
                  setHandoff(next);
                }}
                value={key}
              />
              <input
                aria-label={`Value for ${key}`}
                className="builder-field flex-1 font-mono"
                onChange={event => setHandoff({ ...handoff, [key]: event.target.value })}
                placeholder="{{outputs.start.data.customer_number}}"
                value={value}
              />
              <button
                aria-label={`Remove ${key}`}
                className="px-1 text-ink-400 hover:text-red-600"
                onClick={() => {
                  const next = { ...handoff };
                  delete next[key];
                  setHandoff(next);
                }}
                type="button"
              >×</button>
            </div>
          ))}
        </div>
        <button
          className="mt-1 text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setHandoff({ ...handoff, ['new_field']: '' })}
          type="button"
        >
          + Add Output
        </button>
      </section>
    </div>
  );
}
