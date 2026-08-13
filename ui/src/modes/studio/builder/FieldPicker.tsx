import { useMemo, useState } from 'react';

import type { ContractField, ContractNode, OutputContract } from '../../../api/types';

/**
 * The typed value tree behind mapping and rules.
 *
 * Clicking a field is the mapping (§14) — the author never types
 * `{{outputs.understand_request.result.customer.company}}`. Each entry shows
 * more than a name (§15): its type, whether it can be unavailable at run time,
 * and what it means, because "this may be null" is the single most useful thing
 * to know before you map a value into a required config field.
 *
 * The tree only ever contains values that can actually reach the selected step:
 * the backend computes that from the graph, so the picker cannot offer a
 * reference that would fail preflight.
 */

const KIND_LABELS: Record<string, string> = {
  ai: 'AI',
  deterministic: 'Rule',
  external: 'External',
  human: 'Human',
  input: 'Input',
  output: 'Output',
};

export function typeLabel(field: Pick<ContractField, 'type' | 'item_type'>): string {
  if (field.type === 'list' && field.item_type) return `List of ${field.item_type}`;
  if (field.type === 'unknown') return 'Untyped';
  return field.type.charAt(0).toUpperCase() + field.type.slice(1);
}

function matches(field: ContractField, query: string): boolean {
  if (!query) return true;
  return `${field.path} ${field.description}`.toLowerCase().includes(query);
}

function FieldEntry({
  field,
  onPick,
  selected,
}: {
  field: ContractField;
  onPick: (field: ContractField) => void;
  selected?: boolean;
}) {
  // Depth from the dotted path, so nesting reads as nesting without the tree
  // having to be rebuilt as a hierarchy the backend already flattened.
  const depth = field.path.split('.').length - 1;
  const leaf = field.path.split('.').slice(-1)[0];

  return (
    <button
      className={`flex w-full items-start gap-2 rounded px-2 py-1 text-left hover:bg-accent-50 ${
        selected ? 'bg-accent-50 ring-1 ring-accent-300' : ''
      }`}
      onClick={() => onPick(field)}
      style={{ paddingLeft: 8 + depth * 12 }}
      title={field.reference}
      type="button"
    >
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[11px] font-medium text-ink-800">
          {leaf}
          {field.enum_values.length > 0 && (
            <span className="ml-1 font-normal text-ink-400">
              {field.enum_values.slice(0, 4).join(' | ')}
              {field.enum_values.length > 4 ? ' | …' : ''}
            </span>
          )}
        </span>
        {field.description && (
          <span className="block truncate text-[10px] text-ink-500">
            {field.description}
          </span>
        )}
      </span>
      <span className="flex-none text-right">
        <span className="block text-[10px] text-ink-500">{typeLabel(field)}</span>
        <span
          className={`block text-[10px] ${
            field.may_be_unavailable ? 'text-amber-600' : 'text-ink-400'
          }`}
        >
          {field.may_be_unavailable ? 'may be empty' : 'always set'}
        </span>
      </span>
    </button>
  );
}

export function FieldPicker({
  contract,
  onPick,
  selectedReference,
  filter,
  emptyHint,
}: {
  contract: OutputContract | null;
  onPick: (field: ContractField) => void;
  selectedReference?: string;
  /** Restrict to fields a particular editor can use — the rule editor passes a
   *  predicate so it never offers a value it has no operator for. */
  filter?: (field: ContractField, node: ContractNode) => boolean;
  emptyHint?: string;
}) {
  const [query, setQuery] = useState('');
  const normalised = query.trim().toLowerCase();

  const groups = useMemo(() => {
    if (!contract) return [];
    return contract.nodes
      .map(node => ({
        node,
        fields: node.fields.filter(
          field => matches(field, normalised) && (!filter || filter(field, node)),
        ),
      }))
      .filter(group => group.fields.length > 0);
  }, [contract, filter, normalised]);

  const inputs = useMemo(() => {
    if (!contract) return [];
    return contract.inputs.filter(
      item => !normalised || `${item.name} ${item.description}`.toLowerCase().includes(normalised),
    );
  }, [contract, normalised]);

  if (!contract) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 p-3 text-center text-[11px] text-ink-500">
        Loading available values…
      </div>
    );
  }

  const nothing = groups.length === 0 && inputs.length === 0;

  return (
    <div>
      <input
        aria-label="Search available values"
        className="builder-field"
        onChange={event => setQuery(event.target.value)}
        placeholder="Search fields"
        type="search"
        value={query}
      />
      <div className="mt-2 max-h-80 space-y-3 overflow-y-auto">
        {inputs.length > 0 && (
          <div>
            <div className="px-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
              Workflow inputs
            </div>
            <div className="mt-1">
              {inputs.map(item => (
                <FieldEntry
                  field={{
                    path: item.name,
                    reference: item.reference,
                    type: item.type,
                    description: item.description,
                    required: item.required,
                    may_be_unavailable: !item.required,
                    enum_values: [],
                    item_type: null,
                    operators: [],
                  }}
                  key={item.reference}
                  onPick={onPick}
                  selected={selectedReference === item.reference}
                />
              ))}
            </div>
          </div>
        )}

        {groups.map(({ node, fields }) => (
          <div key={node.node_id}>
            <div className="flex items-center justify-between px-1">
              <span className="truncate text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                {node.label}
              </span>
              <span className="flex-none rounded-full bg-slate-100 px-1.5 text-[9px] text-ink-500">
                {KIND_LABELS[node.execution_kind] ?? node.execution_kind}
              </span>
            </div>
            {!node.typed && (
              <div className="px-1 text-[10px] text-ink-400">
                This step&apos;s output is not typed, so field checks cannot
                verify references into it.
              </div>
            )}
            <div className="mt-1">
              {fields.map(field => (
                <FieldEntry
                  field={field}
                  key={field.reference}
                  onPick={onPick}
                  selected={selectedReference === field.reference}
                />
              ))}
            </div>
          </div>
        ))}

        {nothing && (
          <div className="rounded-md border border-dashed border-slate-300 p-3 text-center text-[11px] text-ink-500">
            {normalised
              ? `No available value matches “${query}”.`
              : emptyHint
                ?? 'No upstream step produces a value this step can read yet. Connect a step above this one.'}
          </div>
        )}
      </div>
    </div>
  );
}

/** Detail card for one mapped value (§15). */
export function FieldDetail({ field }: { field: ContractField }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white p-2">
      <div className="text-[11px] font-semibold text-ink-900">
        {field.path.split('.').slice(-1)[0]}
      </div>
      <div className="mt-0.5 break-all font-mono text-[10px] text-accent-700">
        {field.reference}
      </div>
      <div className="mt-1 text-[10px] text-ink-500">
        {typeLabel(field)}
        {' · '}
        {field.may_be_unavailable ? 'Optional — can be empty at run time' : 'Always present'}
      </div>
      {field.description && (
        <div className="mt-1 text-[10px] leading-4 text-ink-600">{field.description}</div>
      )}
    </div>
  );
}
