import { useCallback, useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type {
  BusinessRule,
  OperatorCatalog,
  OutputContract,
  RuleAction,
  RuleConditionGroup,
} from '../../../api/types';
import {
  ConditionGroupEditor,
  coerceValue,
  isGroup,
  newGroup,
  stripBraces,
  valueToText,
} from './ConditionGroupEditor';
import { InfoPopover } from './InfoPopover';

/**
 * The no-code rule editor.
 *
 * Builds the deterministic IF/THEN rules the Decision node evaluates:
 *
 *     IF   intent equals "technical_support"
 *     AND  confidence is at least 0.80
 *     THEN route = "priority_support"
 *
 * Conditions are edited through the shared group editor, which sources its
 * fields from the upstream output contract and its operators from that field's
 * declared type — so a rule cannot reference a value that does not exist, or
 * use an operator the runtime would refuse.
 */

export function newRule(name = 'New rule'): BusinessRule {
  return {
    name,
    description: '',
    when: newGroup(),
    then: [{ field: '', operation: 'set', value: '' }],
  };
}

function ActionEditor({
  action,
  onChange,
  onRemove,
}: {
  action: RuleAction;
  onChange: (next: RuleAction) => void;
  onRemove: () => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex-none text-[11px] text-ink-500">Set</span>
      <input
        aria-label="Field to set"
        className="builder-field flex-1 font-mono"
        onChange={event => onChange({ ...action, field: event.target.value })}
        placeholder="human_review"
        value={action.field}
      />
      <select
        aria-label="Operation"
        className="builder-field w-32 flex-none"
        onChange={event => onChange({
          ...action,
          operation: event.target.value as RuleAction['operation'],
        })}
        value={action.operation ?? 'set'}
      >
        <option value="set">to</option>
        <option value="append">append</option>
        <option value="increase">increase by</option>
        <option value="decrease">decrease by</option>
      </select>
      <input
        aria-label="Value"
        className="builder-field w-44 flex-none"
        onChange={event => onChange({ ...action, value: coerceValue(event.target.value) })}
        placeholder="true"
        value={valueToText(action.value)}
      />
      <button
        aria-label="Remove action"
        className="flex-none px-1 text-ink-400 hover:text-red-600"
        onClick={onRemove}
        type="button"
      >×</button>
    </div>
  );
}

function RuleCard({
  rule,
  index,
  contract,
  operators,
  onChange,
  onRemove,
}: {
  rule: BusinessRule;
  index: number;
  contract: OutputContract | null;
  operators: OperatorCatalog | null;
  onChange: (next: BusinessRule) => void;
  onRemove: () => void;
}) {
  const [open, setOpen] = useState(true);

  return (
    <div className="rounded-lg border border-slate-200 bg-white">
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-2">
        <button
          aria-label={open ? 'Collapse rule' : 'Expand rule'}
          className="flex-none text-ink-400 hover:text-ink-800"
          onClick={() => setOpen(value => !value)}
          type="button"
        >
          {open ? '▾' : '▸'}
        </button>
        <span className="flex-none text-[10px] font-semibold uppercase tracking-wide text-ink-400">
          Rule {index + 1}
        </span>
        <input
          aria-label="Rule name"
          className="builder-field flex-1"
          onChange={event => onChange({ ...rule, name: event.target.value })}
          placeholder="Low confidence needs a person"
          value={rule.name}
        />
        <button
          aria-label={`Remove rule ${rule.name}`}
          className="flex-none px-1 text-ink-400 hover:text-red-600"
          onClick={onRemove}
          type="button"
        >×</button>
      </div>

      {open && (
        <div className="space-y-3 p-3">
          <label className="flex items-center gap-2 text-[11px] text-ink-700">
            <input
              checked={Boolean(rule.default)}
              onChange={event => onChange({
                ...rule,
                default: event.target.checked,
                // A default rule always applies, so it must not also carry
                // conditions — the runtime rejects that combination outright.
                when: event.target.checked ? null : (rule.when ?? newGroup()),
              })}
              type="checkbox"
            />
            Always apply (no conditions)
          </label>

          {!rule.default && (
            <div>
              <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                If
              </div>
              <ConditionGroupEditor
                contract={contract}
                group={rule.when ?? newGroup()}
                onChange={when => onChange({ ...rule, when })}
                operators={operators}
              />
            </div>
          )}

          <div>
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
              Then
            </div>
            <div className="space-y-1.5">
              {rule.then.map((action, position) => (
                <ActionEditor
                  action={action}
                  key={position}
                  onChange={next => {
                    const then = [...rule.then];
                    then[position] = next;
                    onChange({ ...rule, then });
                  }}
                  onRemove={() => onChange({
                    ...rule,
                    then: rule.then.filter((_, other) => other !== position),
                  })}
                />
              ))}
            </div>
            <button
              className="mt-1 text-[11px] font-medium text-accent-700 hover:underline"
              onClick={() => onChange({
                ...rule,
                then: [...rule.then, { field: '', operation: 'set', value: '' }],
              })}
              type="button"
            >
              + Action
            </button>
          </div>

          <label className="block text-[11px] font-medium text-ink-700">
            Why this rule exists (shown in the run explanation)
            <input
              className="builder-field mt-1"
              onChange={event => onChange({ ...rule, description: event.target.value })}
              placeholder="Below 0.80 we do not act on the extraction automatically."
              value={rule.description ?? ''}
            />
          </label>
        </div>
      )}
    </div>
  );
}

export function RuleBuilder({
  rules,
  contract,
  operators,
  onChange,
}: {
  rules: BusinessRule[];
  contract: OutputContract | null;
  operators: OperatorCatalog | null;
  onChange: (rules: BusinessRule[]) => void;
}) {
  const [assistOpen, setAssistOpen] = useState(false);

  return (
    <section className="mt-4">
      <div className="flex items-center justify-between">
        <div className="builder-panel-heading flex items-center gap-1.5">
          Business rules
          <InfoPopover feature="conditional_routing" />
        </div>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setAssistOpen(true)}
          type="button"
        >
          Describe a rule
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Evaluated in order; every matching rule applies. No model is involved —
        the same facts always produce the same conclusions, and each run records
        which conditions matched.
      </p>

      <div className="mt-3 space-y-2">
        {rules.map((rule, index) => (
          <RuleCard
            contract={contract}
            index={index}
            key={index}
            onChange={next => {
              const copy = [...rules];
              copy[index] = next;
              onChange(copy);
            }}
            onRemove={() => onChange(rules.filter((_, position) => position !== index))}
            operators={operators}
            rule={rule}
          />
        ))}
      </div>

      <button
        className="mt-2 w-full rounded border border-dashed border-slate-300 py-2 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => onChange([...rules, newRule(`Rule ${rules.length + 1}`)])}
        type="button"
      >
        + Add rule
      </button>

      {assistOpen && (
        <RuleAssistant
          contract={contract}
          onApply={proposed => {
            onChange([...rules, ...proposed]);
            setAssistOpen(false);
          }}
          onClose={() => setAssistOpen(false)}
        />
      )}
    </section>
  );
}

/**
 * Turn a described rule into deterministic configuration (§36).
 *
 * The model writes the rule once; the rule engine evaluates it every run. The
 * proposal is validated server-side against the runtime's own rule model, so
 * anything the engine could not evaluate is reported as rejected rather than
 * quietly landing in the editor.
 */
function RuleAssistant({
  contract,
  onApply,
  onClose,
}: {
  contract: OutputContract | null;
  onApply: (rules: BusinessRule[]) => void;
  onClose: () => void;
}) {
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [proposal, setProposal] = useState<BusinessRule[] | null>(null);
  const [notes, setNotes] = useState('');

  const availableFields = useMemo(() => {
    if (!contract) return [];
    return contract.nodes.flatMap(node => node.fields.map(field => ({
      path: stripBraces(field.reference),
      reference: stripBraces(field.reference),
      type: field.type,
      enum_values: field.enum_values,
    })));
  }, [contract]);

  const submit = useCallback(() => {
    setBusy(true);
    setError(null);
    api.suggestRules({ description, available_fields: availableFields })
      .then(result => {
        setProposal(result.rules);
        setNotes(result.notes ?? '');
        if (result.rules.length === 0) {
          setError(
            result.notes
              || 'That rule could not be expressed against the values this step can read.',
          );
        }
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  }, [availableFields, description]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4">
      <div className="max-h-[85vh] w-full max-w-2xl overflow-y-auto rounded-lg bg-white p-5 shadow-xl">
        <div className="flex items-start justify-between">
          <div>
            <h3 className="text-sm font-semibold text-ink-900">
              Describe the business rule
            </h3>
            <p className="mt-1 text-[11px] text-ink-500">
              You review the result before it is added. Once added it is an
              ordinary deterministic rule — nothing consults a model at run time.
            </p>
          </div>
          <button
            aria-label="Close"
            className="text-ink-400 hover:text-ink-900"
            onClick={onClose}
            type="button"
          >×</button>
        </div>

        <textarea
          className="builder-field mt-4"
          onChange={event => setDescription(event.target.value)}
          placeholder="If the customer says production has stopped, mark the request as critical."
          rows={3}
          value={description}
        />

        {error && (
          <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
            {error}
          </div>
        )}

        {proposal && proposal.length > 0 && (
          <div className="mt-4 rounded-md border border-slate-200 p-3">
            <div className="text-[11px] font-semibold text-ink-800">Proposed</div>
            {notes && <p className="mt-1 text-[11px] text-ink-500">{notes}</p>}
            {proposal.map((rule, index) => (
              <pre
                className="mt-2 overflow-x-auto rounded bg-slate-50 p-2 font-mono text-[10px] text-ink-700"
                key={index}
              >
                {renderRule(rule)}
              </pre>
            ))}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button className="ui-button ui-button--ghost" onClick={onClose} type="button">
            Cancel
          </button>
          <button
            className="ui-button ui-button--secondary"
            disabled={busy || !description.trim()}
            onClick={submit}
            type="button"
          >
            {busy ? 'Thinking…' : 'Propose rule'}
          </button>
          {proposal && proposal.length > 0 && (
            <button
              className="ui-button ui-button--primary"
              onClick={() => onApply(proposal)}
              type="button"
            >
              Add {proposal.length === 1 ? 'this rule' : `these ${proposal.length} rules`}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

/** Render a rule the way the author reads it, not as raw JSON. */
export function renderRule(rule: BusinessRule): string {
  const lines: string[] = [`RULE  ${rule.name}`];
  if (rule.default) {
    lines.push('ALWAYS');
  } else if (rule.when) {
    lines.push('IF');
    lines.push(...renderGroup(rule.when, 1));
  }
  lines.push('THEN');
  for (const action of rule.then) {
    const verb = action.operation && action.operation !== 'set' ? ` ${action.operation}` : ' =';
    lines.push(`  ${action.field}${verb} ${JSON.stringify(action.value ?? null)}`);
  }
  return lines.join('\n');
}

function renderGroup(group: RuleConditionGroup, depth: number): string[] {
  const pad = '  '.repeat(depth);
  const joiner = group.operator.toUpperCase();
  const lines: string[] = [];
  group.conditions.forEach((item, index) => {
    if (group.operator === 'not' && index === 0) lines.push(`${pad}NOT`);
    else if (index > 0) lines.push(`${pad}${joiner}`);
    if (isGroup(item)) {
      lines.push(`${pad}(`);
      lines.push(...renderGroup(item, depth + 1));
      lines.push(`${pad})`);
    } else {
      const value = item.value === undefined || item.value === null
        ? ''
        : ` ${JSON.stringify(item.value)}`;
      lines.push(`${pad}${item.field} ${item.operator}${value}`);
    }
  });
  return lines;
}
