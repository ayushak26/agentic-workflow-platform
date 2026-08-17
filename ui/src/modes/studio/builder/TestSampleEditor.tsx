import { useState } from 'react';

import type { ContractField, OutputContract } from '../../../api/types';
import { coerceValue, valueToText } from './ConditionGroupEditor';

/**
 * Choose-or-write controls for the Test tab's two sample sections —
 * "Sample workflow inputs" and "Sample results from earlier steps" — instead
 * of a blank JSON textarea an author has to hand-build by looking up field
 * names elsewhere in the Builder. One row per declared input/field, each
 * with a control shaped like its actual type; a JSON object/list field still
 * gets a small textarea, because no single-line control can represent one
 * honestly.
 */

//: Past this many fields, a node's section starts collapsed — mirrors
// FieldPicker's own FIELD_GROUP_COLLAPSE_THRESHOLD for the same reason (an
// MCP tool's full nested response is a lot of rows to scroll past).
const FIELD_GROUP_COLLAPSE_THRESHOLD = 8;

function getDeep(target: Record<string, unknown>, path: string): unknown {
  let cursor: unknown = target;
  for (const key of path.split('.')) {
    if (typeof cursor !== 'object' || cursor === null) return undefined;
    cursor = (cursor as Record<string, unknown>)[key];
  }
  return cursor;
}

function setDeepImmutable(
  target: Record<string, unknown>,
  path: string,
  value: unknown,
): Record<string, unknown> {
  const segments = path.split('.');
  const [head, ...rest] = segments;
  if (rest.length === 0) return { ...target, [head]: value };
  const existing = target[head];
  const nested = typeof existing === 'object' && existing !== null && !Array.isArray(existing)
    ? existing as Record<string, unknown>
    : {};
  return { ...target, [head]: setDeepImmutable(nested, rest.join('.'), value) };
}

function FieldControl({
  field,
  value,
  onChange,
}: {
  field: Pick<ContractField, 'type' | 'enum_values' | 'item_type'>;
  value: unknown;
  onChange: (next: unknown) => void;
}) {
  if (field.type === 'boolean') {
    return (
      <select
        className="builder-field mt-1"
        onChange={event => onChange(event.target.value === 'true')}
        value={value === true ? 'true' : 'false'}
      >
        <option value="false">false</option>
        <option value="true">true</option>
      </select>
    );
  }

  if (field.type === 'enum' && field.enum_values.length > 0) {
    return (
      <select
        className="builder-field mt-1"
        onChange={event => onChange(event.target.value || undefined)}
        value={typeof value === 'string' ? value : ''}
      >
        <option value="">(none)</option>
        {field.enum_values.map(item => <option key={item} value={item}>{item}</option>)}
      </select>
    );
  }

  if (field.type === 'number' || field.type === 'integer') {
    return (
      <input
        className="builder-field mt-1"
        onChange={event => onChange(event.target.value === '' ? undefined : coerceValue(event.target.value, field.type))}
        type="number"
        value={typeof value === 'number' ? value : ''}
      />
    );
  }

  // List<Enum>: checkboxes over the closed set, not raw text — the value
  // reaching execution is still a real array either way, but nobody should
  // have to remember and correctly spell every allowed value by hand.
  if (field.type === 'list' && field.item_type === 'enum' && field.enum_values.length > 0) {
    const selected = new Set(Array.isArray(value) ? value as string[] : []);
    return (
      <div className="mt-1 space-y-1 rounded-md border border-slate-200 p-2">
        {field.enum_values.map(item => (
          <label className="flex items-center gap-2 text-[11px] font-normal text-ink-700" key={item}>
            <input
              checked={selected.has(item)}
              onChange={event => {
                const next = new Set(selected);
                if (event.target.checked) next.add(item);
                else next.delete(item);
                onChange(Array.from(next));
              }}
              type="checkbox"
            />
            <span className="font-mono">{item}</span>
          </label>
        ))}
      </div>
    );
  }

  if (field.type === 'list') {
    return (
      <input
        className="builder-field mt-1"
        onChange={event => onChange(
          event.target.value === ''
            ? []
            : event.target.value.split(',').map(item => item.trim()).filter(Boolean),
        )}
        placeholder="one value, another value"
        type="text"
        value={Array.isArray(value) ? value.join(', ') : ''}
      />
    );
  }

  return (
    <input
      className="builder-field mt-1"
      onChange={event => onChange(event.target.value)}
      type="text"
      value={typeof value === 'string' ? value : valueToText(value)}
    />
  );
}

/** One row per declared workflow input (skips `file` — nothing pasteable
 *  stands in for a real upload). */
export function WorkflowInputsEditor({
  contract,
  values,
  onChange,
}: {
  contract: OutputContract | null;
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const inputs = (contract?.inputs ?? []).filter(input => input.type !== 'file');

  if (inputs.length === 0) {
    return (
      <p className="mt-2 text-[11px] text-ink-500">
        This workflow declares no text or JSON inputs.
      </p>
    );
  }

  return (
    <div className="mt-2 space-y-2">
      {inputs.map(input => (
        <label className="block text-[11px] font-medium text-ink-700" key={input.name}>
          <span className="font-mono">{input.name}</span>
          {input.required && <span className="ml-1 text-red-500">*</span>}
          {input.description && (
            <span className="ml-1 font-normal text-ink-400">— {input.description}</span>
          )}
          <FieldControl
            field={{ type: input.type === 'json' ? 'object' : 'string', enum_values: [], item_type: null }}
            onChange={next => onChange({ ...values, [input.name]: next })}
            value={values[input.name]}
          />
        </label>
      ))}
    </div>
  );
}

/** One collapsible section per upstream step, one row per typed field it
 *  produces — the same per-node grouping and collapse-when-large behavior
 *  as the field picker (FieldPicker.tsx), for the same reason: an MCP tool's
 *  whole nested response is too many rows to default open. */
export function UpstreamSampleEditor({
  contract,
  values,
  onChange,
}: {
  contract: OutputContract | null;
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const nodes = (contract?.nodes ?? []).filter(node => node.fields.length > 0);

  if (nodes.length === 0) {
    return (
      <p className="mt-2 text-[11px] text-ink-500">
        No upstream step produces a typed value yet — connect one, or use
        Advanced below.
      </p>
    );
  }

  return (
    <div className="mt-2 space-y-2">
      {nodes.map(node => {
        const collapsible = node.fields.length > FIELD_GROUP_COLLAPSE_THRESHOLD;
        const isOpen = !collapsible || expanded.has(node.node_id);
        const nodeValues = (values[node.node_id] as Record<string, unknown> | undefined) ?? {};
        return (
          <div className="rounded-md border border-slate-200 p-2" key={node.node_id}>
            <button
              className="flex w-full items-center justify-between gap-2 text-left text-[11px] font-semibold text-ink-800"
              onClick={() => collapsible && setExpanded(current => {
                const next = new Set(current);
                if (next.has(node.node_id)) next.delete(node.node_id);
                else next.add(node.node_id);
                return next;
              })}
              type="button"
            >
              <span className="truncate">
                {collapsible && <span aria-hidden="true">{isOpen ? '▾ ' : '▸ '}</span>}
                {node.label} <span className="font-mono text-[10px] font-normal text-ink-400">({node.node_id})</span>
              </span>
              <span className="flex-none text-[10px] font-normal text-ink-400">
                {node.fields.length} field{node.fields.length === 1 ? '' : 's'}
              </span>
            </button>
            {isOpen && (
              <div className="mt-2 space-y-2">
                {node.fields.map(field => (
                  <label className="block text-[10px] font-medium text-ink-600" key={field.path}>
                    <span className="font-mono">{field.path}</span>
                    <FieldControl
                      field={field}
                      onChange={next => onChange({
                        ...values,
                        [node.node_id]: setDeepImmutable(nodeValues, field.path, next),
                      })}
                      value={getDeep(nodeValues, field.path)}
                    />
                  </label>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
