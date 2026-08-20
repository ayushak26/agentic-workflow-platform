import { useMemo, useState } from 'react';

import type { ContractField, ContractNode, OutputContract } from '../../../api/types';
import { humanizeIdentifier } from '../guided/runtime-model';
import {
  asContractField,
  buildRecommended,
  incompatibilityReason,
  isTypeCompatible,
  tokenize,
  typeLabel,
} from './field-suggest';
import type { DestinationKind } from './field-suggest';

// Re-exported for existing call sites (`OutputsPanel.tsx`, `PromptTemplateConfig.tsx`)
// that import these from here rather than from `field-suggest` directly.
export { typeLabel };
export type { DestinationKind };

/**
 * The value picker behind every "use data from an earlier step" moment in
 * the Builder — the Inputs tab, tool arguments, rule conditions, router
 * fields, and (via `TemplateTextField`) the "+ Insert data" button inside
 * prompt/email text.
 *
 * The interaction is always: click a value, done. No confirm step. What
 * changes per call site is only *which* values look like a good match —
 * `destinationKind` hides values that can't work at all (an object can't
 * fill a text field), and `destinationLabel`/`destinationHint` surface a
 * short "Recommended" shortlist so an author rarely has to browse the full
 * list. The backend never sees any of this: `onPick` still just hands back
 * the same `ContractField.reference` it always did, and the tree only ever
 * contains values that can actually reach the selected step — the backend
 * computes that from the graph, so the picker cannot offer a reference that
 * would fail preflight.
 */

const KIND_LABELS: Record<string, string> = {
  ai: 'AI',
  deterministic: 'Rule',
  external: 'External',
  human: 'Human',
  input: 'Input',
  output: 'Output',
};

//: A group past this many compatible values starts collapsed — see ValuePicker.
const FIELD_GROUP_COLLAPSE_THRESHOLD = 8;

function matches(field: ContractField, node: ContractNode | undefined, query: string): boolean {
  if (!query) return true;
  const haystack = tokenize(
    `${field.path} ${field.description} ${node?.label ?? ''} ${humanizeIdentifier(field.path.split('.').slice(-1)[0] ?? '')}`,
  );
  return haystack.some(token => token.includes(query)) || `${field.path} ${field.description}`.toLowerCase().includes(query);
}

function FieldEntry({
  field,
  onPick,
  selected,
  stepLabel,
  preview,
  previewIsLive,
  reason,
  recommended,
}: {
  field: ContractField;
  onPick: (field: ContractField) => void;
  selected?: boolean;
  /** Shown under the label — only needed where rows from different steps mix (Recommended). */
  stepLabel?: string;
  preview?: string | null;
  previewIsLive?: boolean;
  /** Set only for a type-incompatible field shown under "Other values". */
  reason?: string;
  recommended?: boolean;
}) {
  const depth = stepLabel ? 0 : Math.max(0, field.path.split('.').length - 1);
  const leaf = field.path.split('.').slice(-1)[0] || field.path;
  const label = humanizeIdentifier(leaf) || leaf;

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
          {recommended && <span aria-hidden="true" className="mr-1">⭐</span>}
          {label}
          {field.enum_values.length > 0 && (
            <span className="ml-1 font-normal text-ink-400">
              {field.enum_values.slice(0, 4).join(' | ')}
              {field.enum_values.length > 4 ? ' | …' : ''}
            </span>
          )}
        </span>
        {stepLabel && (
          <span className="block truncate text-[10px] text-accent-700">{stepLabel}</span>
        )}
        {reason ? (
          <span className="block truncate text-[10px] text-amber-600">{reason}</span>
        ) : preview ? (
          <span className="block truncate text-[10px] text-ink-500">
            {previewIsLive
              ? <><span className="font-semibold text-emerald-600">Ran: </span>&ldquo;{preview}&rdquo;</>
              : preview}
          </span>
        ) : null}
      </span>
      <span className="flex-none text-right">
        <span className="block text-[10px] text-ink-400">{typeLabel(field)}</span>
        {field.may_be_unavailable && !reason && (
          <span className="block text-[10px] text-amber-600">may be empty</span>
        )}
      </span>
    </button>
  );
}

export function ValuePicker({
  contract,
  onPick,
  selectedReference,
  filter,
  emptyHint,
  destinationLabel,
  destinationHint,
  destinationKind = 'any',
  previewValues,
}: {
  contract: OutputContract | null;
  onPick: (field: ContractField) => void;
  selectedReference?: string;
  /** Restrict to fields a particular editor can use — the rule editor passes a
   *  predicate so it never offers a value it has no operator for. */
  filter?: (field: ContractField, node: ContractNode) => boolean;
  emptyHint?: string;
  /** What this value is for, e.g. "Customer message" — drives "Recommended" and the heading. */
  destinationLabel?: string;
  /** Extra context for ranking, e.g. the target field's own description. */
  destinationHint?: string;
  /** Hides values that can't work here at all; 'any' (default) shows everything, unfiltered. */
  destinationKind?: DestinationKind;
  /** node_id -> field path -> a real value from the last time that step ran. */
  previewValues?: Record<string, Record<string, string>>;
}) {
  const [query, setQuery] = useState('');
  const normalised = query.trim().toLowerCase();
  // A node with many fields (an MCP tool's whole nested response, a large
  // extraction schema) can push a dozen-plus rows into this list — enough
  // that its own values push every other step's out of view below the fold.
  // Groups past this size start collapsed; a search in progress always shows
  // every match, uncollapsed, since a hidden result would look like a miss.
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const filterableGroups = useMemo(() => {
    if (!contract) return [];
    return contract.nodes
      .map(node => ({
        node,
        fields: node.fields.filter(field => !filter || filter(field, node)),
      }))
      .filter(group => group.fields.length > 0);
  }, [contract, filter]);

  const groups = useMemo(
    () => filterableGroups
      .map(group => ({
        node: group.node,
        fields: group.fields.filter(field => matches(field, group.node, normalised)),
      }))
      .filter(group => group.fields.length > 0),
    [filterableGroups, normalised],
  );

  const inputs = useMemo(() => {
    if (!contract) return [];
    return contract.inputs.filter(
      item => !normalised || `${item.name} ${item.description}`.toLowerCase().includes(normalised),
    );
  }, [contract, normalised]);

  const recommended = useMemo(
    () => (normalised ? [] : buildRecommended(filterableGroups, contract?.inputs ?? [], destinationLabel, destinationHint, destinationKind)),
    [filterableGroups, contract, destinationLabel, destinationHint, destinationKind, normalised],
  );

  if (!contract) {
    return (
      <div className="rounded-md border border-dashed border-slate-300 p-3 text-center text-[11px] text-ink-500">
        Loading available values…
      </div>
    );
  }

  const nothing = groups.length === 0 && inputs.length === 0;

  const previewFor = (field: ContractField, node: ContractNode | null): { preview: string | null; live: boolean } => {
    const live = node ? previewValues?.[node.node_id]?.[field.path] : undefined;
    if (live !== undefined) return { preview: live, live: true };
    return { preview: field.description || null, live: false };
  };

  return (
    <div>
      <input
        aria-label="Search available data"
        className="builder-field"
        onChange={event => setQuery(event.target.value)}
        placeholder="Search available data…"
        type="search"
        value={query}
      />
      <div className="mt-2 max-h-80 space-y-3 overflow-y-auto">
        {recommended.length > 0 && (
          <div>
            <div className="px-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
              Recommended
            </div>
            <div className="mt-1">
              {recommended.map(({ field, node }) => {
                const { preview, live } = previewFor(field, node);
                return (
                  <FieldEntry
                    field={field}
                    key={`recommended-${field.reference}`}
                    onPick={onPick}
                    preview={preview}
                    previewIsLive={live}
                    recommended
                    selected={selectedReference === field.reference}
                    stepLabel={node ? node.label : 'Workflow Input'}
                  />
                );
              })}
            </div>
          </div>
        )}

        {inputs.length > 0 && (() => {
          const compatible = inputs.filter(item => isTypeCompatible(asContractField(item), destinationKind));
          const incompatible = inputs.filter(item => !isTypeCompatible(asContractField(item), destinationKind));
          return (
            <div>
              <div className="flex items-center justify-between px-1">
                <span className="truncate text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                  Workflow Inputs
                </span>
                <span className="flex-none text-[9px] text-ink-400">
                  {compatible.length} value{compatible.length === 1 ? '' : 's'}
                </span>
              </div>
              <div className="mt-1">
                {compatible.map(item => {
                  const field = asContractField(item);
                  return (
                    <FieldEntry
                      field={field}
                      key={field.reference}
                      onPick={onPick}
                      preview={field.description || null}
                      selected={selectedReference === field.reference}
                    />
                  );
                })}
              </div>
              {incompatible.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer px-1 text-[10px] text-ink-400">
                    Other values ({incompatible.length})
                  </summary>
                  <div className="mt-1">
                    {incompatible.map(item => {
                      const field = asContractField(item);
                      return (
                        <FieldEntry
                          field={field}
                          key={field.reference}
                          onPick={onPick}
                          reason={incompatibilityReason(field, destinationKind)}
                          selected={selectedReference === field.reference}
                        />
                      );
                    })}
                  </div>
                </details>
              )}
            </div>
          );
        })()}

        {groups.map(({ node, fields }) => {
          const compatible = fields.filter(field => isTypeCompatible(field, destinationKind));
          const incompatible = fields.filter(field => !isTypeCompatible(field, destinationKind));
          const collapsible = compatible.length > FIELD_GROUP_COLLAPSE_THRESHOLD;
          const expanded = !collapsible || Boolean(normalised) || expandedGroups.has(node.node_id);
          const fieldList = (
            <div className="mt-1">
              {compatible.map(field => {
                const { preview, live } = previewFor(field, node);
                return (
                  <FieldEntry
                    field={field}
                    key={field.reference}
                    onPick={onPick}
                    preview={preview}
                    previewIsLive={live}
                    selected={selectedReference === field.reference}
                  />
                );
              })}
            </div>
          );
          return (
            <div key={node.node_id}>
              <div className="flex items-center justify-between px-1">
                {collapsible ? (
                  <button
                    className="flex min-w-0 items-center gap-1 truncate text-[10px] font-semibold uppercase tracking-wide text-ink-500 hover:text-ink-800"
                    onClick={() => setExpandedGroups(current => {
                      const next = new Set(current);
                      if (next.has(node.node_id)) next.delete(node.node_id);
                      else next.add(node.node_id);
                      return next;
                    })}
                    type="button"
                  >
                    <span aria-hidden="true">{expanded ? '▾' : '▸'}</span>
                    <span className="truncate">{node.label}</span>
                  </button>
                ) : (
                  <span className="truncate text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                    {node.label}
                  </span>
                )}
                <span className="flex flex-none items-center gap-1">
                  <span className="text-[9px] text-ink-400">
                    {compatible.length} value{compatible.length === 1 ? '' : 's'}
                  </span>
                  <span className="rounded-full bg-slate-100 px-1.5 text-[9px] text-ink-500">
                    {KIND_LABELS[node.execution_kind] ?? node.execution_kind}
                  </span>
                </span>
              </div>
              {!node.typed && (
                <div className="px-1 text-[10px] text-ink-400">
                  This step&apos;s output is not typed, so field checks cannot
                  verify references into it.
                </div>
              )}
              {expanded && fieldList}
              {incompatible.length > 0 && (
                <details className="mt-1">
                  <summary className="cursor-pointer px-1 text-[10px] text-ink-400">
                    Other values in {node.label} ({incompatible.length})
                  </summary>
                  <div className="mt-1">
                    {incompatible.map(field => (
                      <FieldEntry
                        field={field}
                        key={field.reference}
                        onPick={onPick}
                        reason={incompatibilityReason(field, destinationKind)}
                        selected={selectedReference === field.reference}
                      />
                    ))}
                  </div>
                </details>
              )}
            </div>
          );
        })}

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

// Back-compat alias — the picker was designed and named before the wider
// redesign; kept so existing call sites (rule/router/path/tool-argument
// editors) need no import change.
export const FieldPicker = ValuePicker;

/** Detail card for one mapped value (§15). */
export function FieldDetail({ field }: { field: ContractField }) {
  const leaf = field.path.split('.').slice(-1)[0] || field.path;
  return (
    <div className="rounded-md border border-slate-200 bg-white p-2">
      <div className="text-[11px] font-semibold text-ink-900">
        {humanizeIdentifier(leaf) || leaf}
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
