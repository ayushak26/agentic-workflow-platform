import { useState } from 'react';

import type { OutputContract } from '../../../api/types';
import { FieldPicker } from './FieldPicker';

/**
 * The Join node's editor (backend type `TextAssemblerAgent` — kept for
 * backward compatibility, see app/nodes/text_assembler.py).
 *
 * Each "branch" is a template string, usually just one `{{node.field}}`
 * reference picked straight from an upstream step, though the field stays a
 * free-text template so an author can mix in a literal heading or combine
 * more than one reference in a single branch. This step only actually runs
 * once every branch listed here has a value — that is what makes it a join,
 * not just a text concatenator — so the editor's whole job is making "which
 * branches am I waiting on" a one-click picking action instead of hand-typed
 * `{{...}}` tokens.
 */

type Config = Record<string, unknown>;

function partsOf(config: Config): string[] {
  return Array.isArray(config.parts) ? config.parts.map(String) : [];
}

const SEPARATOR_PRESETS: Array<{ label: string; value: string }> = [
  { label: 'Blank line', value: '\n\n' },
  { label: 'New line', value: '\n' },
  { label: 'Divider', value: '\n\n---\n\n' },
  { label: 'Comma', value: ', ' },
];

function BranchField({
  contract,
  index,
  onChange,
  onRemove,
  part,
}: {
  contract: OutputContract | null;
  index: number;
  onChange: (next: string) => void;
  onRemove: () => void;
  part: string;
}) {
  const [picking, setPicking] = useState(false);

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">
          Branch {index + 1}
        </span>
        <button
          aria-label={`Remove branch ${index + 1}`}
          className="flex-none px-1 text-ink-400 hover:text-red-600"
          onClick={onRemove}
          type="button"
        >×</button>
      </div>
      <textarea
        className="builder-field w-full font-mono"
        onChange={event => onChange(event.target.value)}
        placeholder="{{some_node.raw}}"
        rows={2}
        value={part}
      />
      <div className="mt-1">
        <button
          className="w-full rounded border border-dashed border-slate-300 px-2 py-1 text-left text-[10px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
          onClick={() => setPicking(value => !value)}
          type="button"
        >
          {picking ? 'Cancel' : '+ Insert a field'}
        </button>
        {picking && (
          <div className="mt-1 rounded border border-slate-200 p-2">
            <FieldPicker
              contract={contract}
              destinationLabel="Branch to join"
              onPick={picked => {
                onChange(`${part}${picked.reference}`);
                setPicking(false);
              }}
            />
          </div>
        )}
      </div>
    </div>
  );
}

export function JoinConfig({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const parts = partsOf(config);
  const separator = typeof config.separator === 'string' ? config.separator : '\n\n';

  return (
    <div>
      <div className="builder-panel-heading">Branches to join</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        One entry per upstream branch that always runs — e.g. from a
        Parallel Split (an unconditional fan-out). This step waits until
        every branch listed here has produced its value, then combines them
        in order below. Not for a Multi-Route's branches: those are
        optional, and this step would wait forever for one that wasn't
        selected — collect those in the workflow's Outputs tab instead.
      </p>

      <div className="mt-3 space-y-2">
        {parts.map((part, index) => (
          <BranchField
            contract={contract}
            index={index}
            key={index}
            onChange={next => {
              const copy = [...parts];
              copy[index] = next;
              onChange({ ...config, parts: copy });
            }}
            onRemove={() => onChange({ ...config, parts: parts.filter((_, i) => i !== index) })}
            part={part}
          />
        ))}
      </div>

      <button
        className="mt-2 w-full rounded border border-dashed border-slate-300 py-2 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => onChange({ ...config, parts: [...parts, ''] })}
        type="button"
      >
        + Add branch
      </button>

      <div className="mt-4">
        <div className="builder-panel-heading">Separator</div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          Placed between each branch's value in the combined result.
        </p>
        <div className="mt-1.5 grid grid-cols-2 gap-1.5">
          {SEPARATOR_PRESETS.map(preset => (
            <button
              className={`rounded-md border p-2 text-left text-[11px] ${
                separator === preset.value
                  ? 'border-accent-400 bg-accent-50'
                  : 'border-slate-200 hover:border-accent-300'
              }`}
              key={preset.value}
              onClick={() => onChange({ ...config, separator: preset.value })}
              type="button"
            >
              {preset.label}
            </button>
          ))}
        </div>
        <input
          aria-label="Custom separator"
          className="builder-field mt-1.5 w-full font-mono"
          onChange={event => onChange({ ...config, separator: event.target.value })}
          placeholder="Custom separator"
          value={separator}
        />
      </div>
    </div>
  );
}
