import { useState } from 'react';

import type { OutputContract } from '../../../api/types';
import { coerceValue, stripBraces, valueToText } from './ConditionGroupEditor';
import { FieldPicker } from './FieldPicker';

/**
 * The Transform node's typed operation editor.
 *
 * Each operation is a flat record (`target`, `operation`, plus whichever of
 * `source` / `sources` / `value` / `multiply_by` that operation kind actually
 * reads) — the backend model has no per-kind subtype, so this editor is what
 * turns "operation" into the right shape of form instead of one raw-JSON
 * textarea for all fourteen kinds. Field requirements below mirror the
 * `operation_has_its_inputs` validator in app/nodes/data_transform.py exactly:
 * getting a kind's fields wrong here means the workflow fails preflight with
 * a message this editor should have made impossible.
 *
 * Paths (`source`, `sources`, and `object`'s `$path` entries) are bare dotted
 * paths like `some_node.data.field` — no `{{ }}` braces — because
 * DataTransformAgent resolves them itself at run time via `resolve_path`
 * rather than through the `{{...}}` templating pass every other node config
 * goes through. `format`'s `value` is the one exception: it is a template
 * string, so its tokens keep the braces.
 */

type Config = Record<string, unknown>;

type OperationKind =
  | 'copy' | 'constant' | 'format' | 'join' | 'coalesce' | 'object'
  | 'select' | 'number' | 'boolean' | 'lowercase' | 'uppercase' | 'trim'
  | 'count' | 'split';

type Operation = {
  target?: string;
  operation?: OperationKind;
  source?: string | null;
  sources?: string[];
  value?: unknown;
  multiply_by?: number | null;
  default?: unknown;
  description?: string;
};

const OPERATION_KINDS: OperationKind[] = [
  'copy', 'constant', 'format', 'join', 'coalesce', 'object', 'select',
  'number', 'boolean', 'lowercase', 'uppercase', 'trim', 'count', 'split',
];

const OPERATION_LABELS: Record<OperationKind, string> = {
  copy: 'Copy a value',
  constant: 'Constant',
  format: 'Format text',
  join: 'Join several values',
  coalesce: 'First non-empty value',
  object: 'Build an object',
  select: 'Select keys from an object',
  number: 'Parse as number',
  boolean: 'Parse as boolean',
  lowercase: 'Lowercase text',
  uppercase: 'Uppercase text',
  trim: 'Trim whitespace',
  count: 'Count items',
  split: 'Split text',
};

const OPERATION_HINTS: Record<OperationKind, string> = {
  copy: 'Copies the value at Source, unchanged.',
  constant: 'Always writes the same literal value.',
  format: 'Fills a text template — insert a field to add a {{path}} token.',
  join: 'Joins the values at Sources with the separator below, skipping blanks.',
  coalesce: 'Reads Sources in order and uses the first one that is not blank.',
  object: 'Builds a nested object. Each entry either reads a field or is a literal.',
  select: 'Reads the object at Source and keeps only the listed keys.',
  number: 'Parses Source into a number, tolerant of units and thousands separators.',
  boolean: 'Parses Source into true/false.',
  lowercase: 'Reads Source as text and lowercases it.',
  uppercase: 'Reads Source as text and uppercases it.',
  trim: 'Reads Source as text and strips leading/trailing whitespace.',
  count: 'Counts the items in Source (list, object, or string length).',
  split: 'Splits Source into a list using the separator below.',
};

const NEEDS_SOURCE = new Set<OperationKind>([
  'copy', 'select', 'number', 'boolean', 'lowercase', 'uppercase', 'trim', 'count', 'split',
]);
const NEEDS_SOURCES = new Set<OperationKind>(['coalesce', 'join']);

function newOperation(index: number): Operation {
  return { target: `field_${index + 1}`, operation: 'copy', source: '' };
}

function operationsOf(config: Config): Operation[] {
  return Array.isArray(config.operations) ? config.operations as Operation[] : [];
}

/** A single bare-path field: a button showing the current path that expands
 *  into a FieldPicker, matching the field-picking pattern used for rule
 *  conditions — an author clicks a value rather than typing a dotted path. */
function PathField({
  contract,
  onChange,
  path,
  placeholder,
}: {
  contract: OutputContract | null;
  onChange: (path: string) => void;
  path: string;
  placeholder?: string;
}) {
  const [picking, setPicking] = useState(false);
  return (
    <div>
      <button
        className="w-full truncate rounded border border-slate-200 px-2 py-1 text-left font-mono text-[11px] text-ink-800 hover:border-accent-600"
        onClick={() => setPicking(value => !value)}
        type="button"
      >
        {path || placeholder || 'Choose a field…'}
      </button>
      {picking && (
        <div className="mt-1 rounded border border-slate-200 p-2">
          <FieldPicker
            contract={contract}
            onPick={picked => {
              onChange(stripBraces(picked.reference));
              setPicking(false);
            }}
            selectedReference={path ? `{{${path}}}` : undefined}
          />
        </div>
      )}
    </div>
  );
}

function SourcesField({
  contract,
  onChange,
  sources,
}: {
  contract: OutputContract | null;
  onChange: (sources: string[]) => void;
  sources: string[];
}) {
  return (
    <div className="space-y-1.5">
      {sources.map((source, index) => (
        <div className="flex items-center gap-1.5" key={index}>
          <div className="flex-1">
            <PathField
              contract={contract}
              onChange={next => onChange(sources.map((item, i) => (i === index ? next : item)))}
              path={source}
            />
          </div>
          <button
            aria-label="Remove source"
            className="flex-none px-1 text-ink-400 hover:text-red-600"
            onClick={() => onChange(sources.filter((_, i) => i !== index))}
            type="button"
          >×</button>
        </div>
      ))}
      <button
        className="w-full rounded border border-dashed border-slate-300 py-1 text-[10px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => onChange([...sources, ''])}
        type="button"
      >
        + Add source
      </button>
    </div>
  );
}

/** `object`'s `value` is a key → entry map; an entry starting with `$` reads a
 *  live field, anything else is a literal — see the docstring for why. */
function ObjectEntriesField({
  contract,
  entries,
  onChange,
}: {
  contract: OutputContract | null;
  entries: Record<string, unknown>;
  onChange: (entries: Record<string, unknown>) => void;
}) {
  const rows = Object.entries(entries);
  const setRow = (index: number, key: string, entry: unknown) => {
    const next = rows.map(([k, v], i) => (i === index ? [key, entry] as const : [k, v] as const));
    onChange(Object.fromEntries(next));
  };
  return (
    <div className="space-y-1.5">
      {rows.map(([key, entry], index) => {
        const isPath = typeof entry === 'string' && entry.startsWith('$');
        return (
          <div className="rounded border border-slate-200 p-1.5" key={index}>
            <div className="flex items-center gap-1.5">
              <input
                aria-label="Key"
                className="builder-field w-32 flex-none font-mono"
                onChange={event => setRow(index, event.target.value, entry)}
                placeholder="key"
                value={key}
              />
              <button
                className="flex-none rounded border border-slate-200 px-1.5 py-0.5 text-[10px] text-ink-600 hover:border-accent-600"
                onClick={() => setRow(index, key, isPath ? '' : '$')}
                type="button"
              >
                {isPath ? 'Field' : 'Literal'}
              </button>
              <button
                aria-label="Remove entry"
                className="flex-none px-1 text-ink-400 hover:text-red-600"
                onClick={() => onChange(Object.fromEntries(rows.filter((_, i) => i !== index)))}
                type="button"
              >×</button>
            </div>
            <div className="mt-1">
              {isPath ? (
                <PathField
                  contract={contract}
                  onChange={next => setRow(index, key, `$${next}`)}
                  path={(entry as string).slice(1)}
                />
              ) : (
                <input
                  aria-label="Literal value"
                  className="builder-field w-full"
                  onChange={event => setRow(index, key, coerceValue(event.target.value))}
                  placeholder="literal value"
                  value={valueToText(entry)}
                />
              )}
            </div>
          </div>
        );
      })}
      <button
        className="w-full rounded border border-dashed border-slate-300 py-1 text-[10px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => onChange({ ...entries, [`key_${rows.length + 1}`]: '$' })}
        type="button"
      >
        + Add entry
      </button>
    </div>
  );
}

function OperationCard({
  contract,
  index,
  onChange,
  onRemove,
  operation,
}: {
  contract: OutputContract | null;
  index: number;
  onChange: (next: Operation) => void;
  onRemove: () => void;
  operation: Operation;
}) {
  const [open, setOpen] = useState(true);
  const kind = operation.operation ?? 'copy';
  const set = (patch: Partial<Operation>) => onChange({ ...operation, ...patch });

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
        <button
          aria-label={open ? 'Collapse operation' : 'Expand operation'}
          className="flex-none text-ink-400 hover:text-ink-800"
          onClick={() => setOpen(value => !value)}
          type="button"
        >
          {open ? '▾' : '▸'}
        </button>
        <span className="flex-none text-[10px] font-semibold uppercase tracking-wide text-ink-400">
          {index + 1}
        </span>
        <input
          aria-label="Target field"
          className="builder-field flex-1 font-mono"
          onChange={event => set({ target: event.target.value })}
          placeholder="output_field_name"
          value={operation.target ?? ''}
        />
        <select
          aria-label="Operation"
          className="builder-field w-44 flex-none"
          onChange={event => set({ operation: event.target.value as OperationKind })}
          value={kind}
        >
          {OPERATION_KINDS.map(item => (
            <option key={item} value={item}>{OPERATION_LABELS[item]}</option>
          ))}
        </select>
        <button
          aria-label={`Remove operation ${index + 1}`}
          className="flex-none px-1 text-ink-400 hover:text-red-600"
          onClick={onRemove}
          type="button"
        >×</button>
      </div>

      {open && (
        <div className="space-y-2 p-3">
          <p className="text-[10px] leading-4 text-ink-500">{OPERATION_HINTS[kind]}</p>

          {NEEDS_SOURCE.has(kind) && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">Source</div>
              <PathField
                contract={contract}
                onChange={source => set({ source })}
                path={operation.source ?? ''}
              />
            </div>
          )}

          {NEEDS_SOURCES.has(kind) && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">Sources</div>
              <SourcesField
                contract={contract}
                onChange={sources => set({ sources })}
                sources={operation.sources ?? []}
              />
            </div>
          )}

          {(kind === 'join' || kind === 'split') && (
            <label className="block">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                Separator
              </span>
              <input
                className="builder-field mt-1 w-full"
                onChange={event => set({ value: event.target.value })}
                placeholder={kind === 'join' ? ', ' : ','}
                value={typeof operation.value === 'string' ? operation.value : ''}
              />
            </label>
          )}

          {kind === 'constant' && (
            <label className="block">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">Value</span>
              <input
                className="builder-field mt-1 w-full"
                onChange={event => set({ value: coerceValue(event.target.value) })}
                placeholder="true, 42, or plain text"
                value={valueToText(operation.value)}
              />
            </label>
          )}

          {kind === 'format' && (
            <div>
              <div className="mb-1 flex items-center justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                  Template
                </span>
              </div>
              <textarea
                className="builder-field w-full font-mono"
                onChange={event => set({ value: event.target.value })}
                placeholder="Attachments: {{some_node.data.field}}"
                rows={3}
                value={typeof operation.value === 'string' ? operation.value : ''}
              />
              <div className="mt-1">
                <PathField
                  contract={contract}
                  onChange={path => set({
                    value: `${typeof operation.value === 'string' ? operation.value : ''}{{${path}}}`,
                  })}
                  path=""
                  placeholder="+ Insert a field"
                />
              </div>
            </div>
          )}

          {kind === 'object' && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                Entries
              </div>
              <ObjectEntriesField
                contract={contract}
                entries={operation.value && typeof operation.value === 'object'
                  ? operation.value as Record<string, unknown>
                  : {}}
                onChange={value => set({ value })}
              />
            </div>
          )}

          {kind === 'select' && (
            <label className="block">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                Keys to keep
              </span>
              <input
                className="builder-field mt-1 w-full font-mono"
                onChange={event => set({
                  value: event.target.value.split(',').map(item => item.trim()).filter(Boolean),
                })}
                placeholder="account_id, account_name"
                value={Array.isArray(operation.value) ? operation.value.join(', ') : ''}
              />
            </label>
          )}

          {kind === 'number' && (
            <label className="block">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                Multiply by (optional)
              </span>
              <input
                className="builder-field mt-1 w-32"
                onChange={event => set({
                  multiply_by: event.target.value === '' ? null : Number(event.target.value),
                })}
                placeholder="e.g. 1000"
                type="number"
                value={operation.multiply_by ?? ''}
              />
            </label>
          )}

          <div className="grid grid-cols-2 gap-2">
            <label className="block">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                Default if blank
              </span>
              <input
                className="builder-field mt-1 w-full"
                onChange={event => set({
                  default: event.target.value === '' ? null : coerceValue(event.target.value),
                })}
                placeholder="(none)"
                value={valueToText(operation.default)}
              />
            </label>
            <label className="block">
              <span className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                Description
              </span>
              <input
                className="builder-field mt-1 w-full"
                onChange={event => set({ description: event.target.value })}
                placeholder="optional note"
                value={operation.description ?? ''}
              />
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

export function DataTransformConfig({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const operations = operationsOf(config);

  return (
    <div>
      <div className="builder-panel-heading">Operations</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Each operation writes one field of this step&apos;s output. No model is
        involved — every operation is deterministic and its result is exact.
      </p>

      <div className="mt-3 space-y-2">
        {operations.map((operation, index) => (
          <OperationCard
            contract={contract}
            index={index}
            key={index}
            onChange={next => {
              const copy = [...operations];
              copy[index] = next;
              onChange({ ...config, operations: copy });
            }}
            onRemove={() => onChange({
              ...config,
              operations: operations.filter((_, position) => position !== index),
            })}
            operation={operation}
          />
        ))}
      </div>

      <button
        className="mt-2 w-full rounded border border-dashed border-slate-300 py-2 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => onChange({ ...config, operations: [...operations, newOperation(operations.length)] })}
        type="button"
      >
        + Add operation
      </button>

      <label className="mt-4 flex items-center gap-2 text-[11px] text-ink-700">
        <input
          checked={Boolean(config.omit_empty)}
          onChange={event => onChange({ ...config, omit_empty: event.target.checked })}
          type="checkbox"
        />
        Drop fields whose value comes out empty
      </label>
    </div>
  );
}
