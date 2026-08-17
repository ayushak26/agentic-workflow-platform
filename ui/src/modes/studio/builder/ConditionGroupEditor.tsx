import { useMemo, useState } from 'react';

import type {
  ContractField,
  OperatorCatalog,
  OutputContract,
  RuleCondition,
  RuleConditionGroup,
} from '../../../api/types';
import { FieldPicker } from './FieldPicker';

/**
 * Nested AND / OR / NOT condition editing.
 *
 * Shared by the Decision node's rules and the Router's condition cases, because
 * they are the same thing: a group of typed tests over upstream values. Sharing
 * it is what guarantees an author sees the same operators, the same field
 * picker and the same nesting in both places.
 *
 * The operators offered for a field come from that field's declared type via
 * the backend catalog, so the editor cannot construct a condition preflight
 * would reject — a list gets `contains`, never `>=`.
 */

export function isGroup(
  item: RuleCondition | RuleConditionGroup,
): item is RuleConditionGroup {
  return 'conditions' in item;
}

export function newCondition(): RuleCondition {
  return { field: '', operator: 'equals', value: '' };
}

export function newGroup(operator: 'and' | 'or' | 'not' = 'and'): RuleConditionGroup {
  return { operator, conditions: [newCondition()] };
}

export function stripBraces(reference: string): string {
  return reference.replace(/^\{\{\s*/, '').replace(/\s*\}\}$/, '');
}

export function toReference(path: string): string {
  return path ? `{{${path}}}` : '';
}

/** Parse a typed value out of a text input.
 *
 * `0.8` must become a number and `true` a boolean: rules are type-checked
 * against the field's declared type, and a quoted "0.8" compared with a number
 * field would be reported as a mismatch the author never intended. */
export function coerceValue(raw: string, fieldType?: string): unknown {
  const trimmed = raw.trim();
  if (fieldType === 'boolean' || trimmed === 'true' || trimmed === 'false') {
    if (trimmed === 'true') return true;
    if (trimmed === 'false') return false;
  }
  if (fieldType === 'number' || fieldType === 'integer') {
    const parsed = Number(trimmed);
    if (trimmed !== '' && !Number.isNaN(parsed)) return parsed;
  }
  if (trimmed !== '' && /^-?\d*\.?\d+$/.test(trimmed)) return Number(trimmed);
  return raw;
}

export function valueToText(value: unknown): string {
  if (value === undefined || value === null) return '';
  if (Array.isArray(value)) return value.join(', ');
  return String(value);
}

/** Look a bare rule path up in the output contract, so the editor knows the
 *  field's type, its allowed values, and whether it can be empty. */
export function findContractField(
  contract: OutputContract | null,
  path: string,
): ContractField | undefined {
  if (!contract || !path) return undefined;
  const reference = toReference(path);
  for (const node of contract.nodes) {
    const match = node.fields.find(item => item.reference === reference);
    if (match) return match;
  }
  return undefined;
}

function ValueInput({
  arity,
  field,
  onChange,
  value,
}: {
  arity: 'one' | 'many';
  field?: ContractField;
  onChange: (value: unknown) => void;
  value: unknown;
}) {
  // A closed set becomes a dropdown. Typing "beschwerde" where the enum says
  // "complaint" is the mistake this removes entirely.
  if (field?.enum_values?.length && arity === 'one') {
    return (
      <select
        aria-label="Value"
        className="builder-field min-w-0 flex-1"
        onChange={event => onChange(event.target.value)}
        value={String(value ?? '')}
      >
        <option value="">Choose…</option>
        {field.enum_values.map(option => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    );
  }

  if (field?.type === 'boolean' && arity === 'one') {
    return (
      <select
        aria-label="Value"
        className="builder-field w-28 flex-none"
        onChange={event => onChange(event.target.value === 'true')}
        value={String(Boolean(value))}
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }

  return (
    <input
      aria-label="Value"
      className="builder-field min-w-0 flex-1"
      onChange={event => onChange(
        arity === 'many'
          ? event.target.value.split(',').map(part => part.trim()).filter(Boolean)
          : coerceValue(event.target.value, field?.type),
      )}
      placeholder={arity === 'many' ? 'value_a, value_b' : 'value'}
      type={field?.type === 'number' || field?.type === 'integer' ? 'number' : 'text'}
      value={valueToText(value)}
    />
  );
}

function ConditionEditor({
  condition,
  contract,
  operators,
  onChange,
  onRemove,
}: {
  condition: RuleCondition;
  contract: OutputContract | null;
  operators: OperatorCatalog | null;
  onChange: (next: RuleCondition) => void;
  onRemove: () => void;
}) {
  const [picking, setPicking] = useState(!condition.field);
  const field = useMemo(
    () => findContractField(contract, condition.field),
    [contract, condition.field],
  );

  const allowed = field?.operators?.length
    ? field.operators
    : operators?.by_type.unknown ?? ['equals'];
  const arity = operators?.arity[condition.operator] ?? 'one';

  return (
    <div className="rounded-md border border-slate-200 bg-white p-2">
      <div className="flex items-center gap-2">
        <button
          className="min-w-0 flex-1 truncate rounded border border-slate-200 px-2 py-1 text-left font-mono text-[11px] text-ink-800 hover:border-accent-600"
          onClick={() => setPicking(value => !value)}
          type="button"
        >
          {condition.field || 'Choose a field…'}
        </button>
        <button
          aria-label="Remove condition"
          className="flex-none px-1 text-ink-400 hover:text-red-600"
          onClick={onRemove}
          type="button"
        >×</button>
      </div>
      {field && (
        <div className="mt-0.5 text-[10px] text-ink-500">
          {field.type}
          {field.may_be_unavailable && ' · may be empty at run time'}
        </div>
      )}

      <div className="mt-1.5 flex items-center gap-2">
        <select
          aria-label="Operator"
          className="builder-field w-40 flex-none"
          onChange={event => onChange({ ...condition, operator: event.target.value })}
          value={condition.operator}
        >
          {allowed.map(operator => (
            <option key={operator} value={operator}>
              {operators?.labels[operator] ?? operator}
            </option>
          ))}
        </select>
        {arity !== 'none' && (
          <ValueInput
            arity={arity}
            field={field}
            onChange={value => onChange({ ...condition, value })}
            value={condition.value}
          />
        )}
      </div>

      {picking && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <FieldPicker
            contract={contract}
            onPick={picked => {
              // Rules address values directly — no {{ }} braces, unlike a
              // config template.
              onChange({
                ...condition,
                field: stripBraces(picked.reference),
                operator: picked.operators[0] ?? 'equals',
              });
              setPicking(false);
            }}
            selectedReference={toReference(condition.field)}
          />
        </div>
      )}
    </div>
  );
}

export function ConditionGroupEditor({
  group,
  contract,
  operators,
  onChange,
  onRemove,
  depth = 0,
}: {
  group: RuleConditionGroup;
  contract: OutputContract | null;
  operators: OperatorCatalog | null;
  onChange: (next: RuleConditionGroup) => void;
  onRemove?: () => void;
  depth?: number;
}) {
  const replace = (index: number, next: RuleCondition | RuleConditionGroup) => {
    const conditions = [...group.conditions];
    conditions[index] = next;
    onChange({ ...group, conditions });
  };
  const remove = (index: number) => onChange({
    ...group,
    conditions: group.conditions.filter((_, position) => position !== index),
  });

  return (
    <div
      className={`rounded-md border p-2 ${
        depth === 0 ? 'border-slate-200 bg-slate-50/50' : 'border-accent-200 bg-accent-50/40'
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          {(['and', 'or', 'not'] as const).map(operator => (
            <button
              className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase ${
                group.operator === operator
                  ? 'bg-accent-600 text-white'
                  : 'bg-white text-ink-600 hover:bg-accent-50'
              }`}
              key={operator}
              onClick={() => onChange({
                ...group,
                operator,
                // NOT negates exactly one thing; keeping extra children would
                // produce a group the runtime refuses to validate.
                conditions: operator === 'not'
                  ? group.conditions.slice(0, 1)
                  : group.conditions,
              })}
              type="button"
            >
              {operator}
            </button>
          ))}
          <span className="ml-1 text-[10px] text-ink-500">
            {group.operator === 'and' && 'all of these must be true'}
            {group.operator === 'or' && 'any of these is enough'}
            {group.operator === 'not' && 'this must NOT be true'}
          </span>
        </div>
        {onRemove && (
          <button
            aria-label="Remove group"
            className="px-1 text-ink-400 hover:text-red-600"
            onClick={onRemove}
            type="button"
          >×</button>
        )}
      </div>

      <div className="mt-2 space-y-1.5">
        {group.conditions.map((item, index) => (
          isGroup(item) ? (
            <ConditionGroupEditor
              contract={contract}
              depth={depth + 1}
              group={item}
              key={index}
              onChange={next => replace(index, next)}
              onRemove={() => remove(index)}
              operators={operators}
            />
          ) : (
            <ConditionEditor
              condition={item}
              contract={contract}
              key={index}
              onChange={next => replace(index, next)}
              onRemove={() => remove(index)}
              operators={operators}
            />
          )
        ))}
        {group.conditions.length === 0 && (
          <div className="rounded border border-dashed border-slate-300 p-2 text-center text-[10px] text-ink-500">
            No conditions yet.
          </div>
        )}
      </div>

      {group.operator !== 'not' && (
        <div className="mt-2 flex gap-2">
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => onChange({
              ...group,
              conditions: [...group.conditions, newCondition()],
            })}
            type="button"
          >
            + Condition
          </button>
          {depth < 3 && (
            <button
              className="text-[11px] font-medium text-accent-700 hover:underline"
              onClick={() => onChange({
                ...group,
                conditions: [
                  ...group.conditions,
                  newGroup(group.operator === 'and' ? 'or' : 'and'),
                ],
              })}
              type="button"
            >
              + Nested group
            </button>
          )}
        </div>
      )}
    </div>
  );
}
