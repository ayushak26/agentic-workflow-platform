import { useMemo, useState } from 'react';

import type {
  ContractField,
  OperatorCatalog,
  OutputContract,
  RuleConditionGroup,
} from '../../../api/types';
import { ConditionGroupEditor, newGroup } from './ConditionGroupEditor';
import { FieldPicker } from './FieldPicker';
import { InfoPopover } from './InfoPopover';

/**
 * The visual router editor.
 *
 * Routing is where a business process becomes legible, so it is configured as a
 * table of outcomes rather than as an expression. Two modes cover the real
 * cases:
 *
 *   field       one branch per value of a classified field
 *               (intent → department: the common case)
 *   conditions  first matching rule group wins
 *               (for a branch that depends on several facts at once)
 *
 * The branch names entered here appear directly on the canvas edges, which is
 * what makes the graph readable as the process instead of as a node diagram.
 */

type Config = Record<string, unknown>;

type Branch = { value: string; route: string };

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function branchesOf(config: Config): Branch[] {
  const raw = config.branches;
  if (!raw || typeof raw !== 'object') return [];
  return Object.entries(raw as Record<string, string>).map(([value, route]) => ({
    value,
    route,
  }));
}

function toBranchMap(branches: Branch[]): Record<string, string> {
  const map: Record<string, string> = {};
  for (const branch of branches) {
    if (branch.value.trim()) map[branch.value.trim()] = branch.route.trim();
  }
  return map;
}

export function RouterEditor({
  config,
  contract,
  operators,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  operators: OperatorCatalog | null;
  onChange: (next: Config) => void;
}) {
  const mode = asString(config.mode, 'field');
  const set = (patch: Config) => onChange({ ...config, ...patch });

  return (
    <div>
      <section>
        <div className="builder-panel-heading flex items-center gap-1.5">
          How should this step branch?
          <InfoPopover feature="conditional_routing" />
        </div>
        <div className="mt-2 grid grid-cols-2 gap-1.5">
          <ModeCard
            active={mode === 'field'}
            description="One branch per value of a classified field. The usual choice."
            label="On a field value"
            onSelect={() => set({ mode: 'field' })}
          />
          <ModeCard
            active={mode === 'conditions'}
            description="First matching set of conditions wins. For branches that depend on several facts."
            label="On conditions"
            onSelect={() => set({ mode: 'conditions' })}
          />
        </div>
        {(mode === 'rule' || mode === 'llm') && (
          <div className="mt-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
            This router uses the{' '}
            <span className="font-mono">{mode}</span> mode, kept for existing
            workflows. Switching to one of the modes above makes the branches
            visible and costs no tokens.
          </div>
        )}
      </section>

      {mode === 'field' && (
        <FieldModeEditor config={config} contract={contract} onChange={onChange} />
      )}
      {mode === 'conditions' && (
        <ConditionsModeEditor
          config={config}
          contract={contract}
          onChange={onChange}
          operators={operators}
        />
      )}

      <section className="mt-4">
        <label className="block text-[11px] font-medium text-ink-700">
          Otherwise, send to
          <input
            className="builder-field mt-1 font-mono"
            onChange={event => set({ fallback: event.target.value })}
            placeholder="human_review"
            value={asString(config.fallback)}
          />
        </label>
        <p className="mt-1 text-[11px] text-ink-500">
          Where anything unmatched goes. Without a fallback an unexpected value
          fails the run instead of reaching a person.
        </p>
      </section>
    </div>
  );
}

function ModeCard({
  active,
  description,
  label,
  onSelect,
}: {
  active: boolean;
  description: string;
  label: string;
  onSelect: () => void;
}) {
  return (
    <button
      className={`rounded-md border p-2 text-left transition ${
        active ? 'border-accent-600 bg-accent-50' : 'border-slate-200 hover:border-accent-400'
      }`}
      onClick={onSelect}
      type="button"
    >
      <div className="text-[11px] font-semibold text-ink-900">{label}</div>
      <div className="mt-0.5 text-[10px] leading-4 text-ink-500">{description}</div>
    </button>
  );
}

function FieldModeEditor({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const [picking, setPicking] = useState(false);
  const routeField = asString(config.route_field);
  const branches = branchesOf(config);

  const field = useMemo(() => {
    if (!contract || !routeField) return undefined;
    for (const node of contract.nodes) {
      const match = node.fields.find(
        item => item.reference === `{{${routeField}}}`,
      );
      if (match) return match;
    }
    return undefined;
  }, [contract, routeField]);

  // A field with declared allowed values can populate its own branch table.
  // That is the difference between an author typing seven intent labels from
  // memory (and misspelling one) and clicking a button.
  const uncovered = (field?.enum_values ?? []).filter(
    value => !branches.some(branch => branch.value === value),
  );

  const setBranches = (next: Branch[]) => onChange({
    ...config,
    branches: toBranchMap(next),
  });

  return (
    <>
      <section className="mt-4">
        <div className="flex items-center justify-between">
          <label className="text-[11px] font-medium text-ink-700">Route using</label>
          <button
            className="text-[11px] font-medium text-accent-700 hover:underline"
            onClick={() => setPicking(value => !value)}
            type="button"
          >
            {picking ? 'Close picker' : 'Pick a field'}
          </button>
        </div>
        <input
          className="builder-field mt-1 font-mono"
          onChange={event => onChange({ ...config, route_field: event.target.value })}
          placeholder="outputs.understand_request.result.intent"
          value={routeField}
        />
        {picking && (
          <div className="mt-2 rounded border border-slate-200 p-2">
            <FieldPicker
              contract={contract}
              onPick={(picked: ContractField) => {
                onChange({
                  ...config,
                  route_field: picked.reference.replace(/^\{\{|\}\}$/g, ''),
                });
                setPicking(false);
              }}
              selectedReference={routeField ? `{{${routeField}}}` : ''}
            />
          </div>
        )}
      </section>

      <section className="mt-4">
        <div className="flex items-center justify-between">
          <div className="text-[11px] font-semibold text-ink-800">Branches</div>
          {uncovered.length > 0 && (
            <button
              className="text-[11px] font-medium text-accent-700 hover:underline"
              onClick={() => setBranches([
                ...branches,
                ...uncovered.map(value => ({ value, route: value })),
              ])}
              type="button"
            >
              Add the {uncovered.length} missing value{uncovered.length === 1 ? '' : 's'}
            </button>
          )}
        </div>

        <div className="mt-2 space-y-1.5">
          {branches.map((branch, index) => (
            <div className="flex items-center gap-2" key={index}>
              {field?.enum_values?.length ? (
                <select
                  aria-label="Value"
                  className="builder-field flex-1 font-mono"
                  onChange={event => {
                    const next = [...branches];
                    next[index] = { ...branch, value: event.target.value };
                    setBranches(next);
                  }}
                  value={branch.value}
                >
                  <option value="">Choose a value…</option>
                  {field.enum_values.map(value => (
                    <option key={value} value={value}>{value}</option>
                  ))}
                </select>
              ) : (
                <input
                  aria-label="Value"
                  className="builder-field flex-1 font-mono"
                  onChange={event => {
                    const next = [...branches];
                    next[index] = { ...branch, value: event.target.value };
                    setBranches(next);
                  }}
                  placeholder="technical_support"
                  value={branch.value}
                />
              )}
              <span className="flex-none text-ink-400">→</span>
              <input
                aria-label="Branch"
                className="builder-field flex-1 font-mono"
                onChange={event => {
                  const next = [...branches];
                  next[index] = { ...branch, route: event.target.value };
                  setBranches(next);
                }}
                placeholder="support"
                value={branch.route}
              />
              <button
                aria-label={`Remove branch ${branch.value}`}
                className="flex-none px-1 text-ink-400 hover:text-red-600"
                onClick={() => setBranches(branches.filter((_, position) => position !== index))}
                type="button"
              >×</button>
            </div>
          ))}
        </div>

        <button
          className="mt-2 w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
          onClick={() => setBranches([...branches, { value: '', route: '' }])}
          type="button"
        >
          + Add branch
        </button>

        <p className="mt-2 text-[11px] leading-4 text-ink-500">
          The branch name on the right appears on the canvas edge and must match
          a step this router connects to.
        </p>
      </section>
    </>
  );
}

function ConditionsModeEditor({
  config,
  contract,
  operators,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  operators: OperatorCatalog | null;
  onChange: (next: Config) => void;
}) {
  const cases = (config.cases as Array<{
    route: string;
    description?: string;
    when?: RuleConditionGroup;
  }> | undefined) ?? [];

  const setCases = (next: typeof cases) => onChange({ ...config, cases: next });

  return (
    <section className="mt-4">
      <div className="text-[11px] font-semibold text-ink-800">
        Cases, in order
      </div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        The first case whose conditions hold wins. Put the safety check first so
        an uncertain request is escalated before anything else is considered.
      </p>

      <div className="mt-2 space-y-2">
        {cases.map((entry, index) => (
          <div className="rounded-md border border-slate-200 bg-white p-2" key={index}>
            <div className="flex items-center gap-2">
              <span className="flex-none text-[10px] font-semibold uppercase text-ink-400">
                {index + 1}
              </span>
              <input
                aria-label="Branch name"
                className="builder-field flex-1 font-mono"
                onChange={event => {
                  const next = [...cases];
                  next[index] = { ...entry, route: event.target.value };
                  setCases(next);
                }}
                placeholder="priority_support"
                value={entry.route}
              />
              <div className="flex flex-none gap-0.5">
                <button
                  aria-label="Move case up"
                  className="px-1 text-ink-400 hover:text-ink-800 disabled:opacity-30"
                  disabled={index === 0}
                  onClick={() => {
                    const next = [...cases];
                    [next[index - 1], next[index]] = [next[index], next[index - 1]];
                    setCases(next);
                  }}
                  type="button"
                >↑</button>
                <button
                  aria-label="Move case down"
                  className="px-1 text-ink-400 hover:text-ink-800 disabled:opacity-30"
                  disabled={index === cases.length - 1}
                  onClick={() => {
                    const next = [...cases];
                    [next[index + 1], next[index]] = [next[index], next[index + 1]];
                    setCases(next);
                  }}
                  type="button"
                >↓</button>
                <button
                  aria-label={`Remove case ${entry.route}`}
                  className="px-1 text-ink-400 hover:text-red-600"
                  onClick={() => setCases(cases.filter((_, position) => position !== index))}
                  type="button"
                >×</button>
              </div>
            </div>

            <input
              aria-label="Why this branch"
              className="builder-field mt-2"
              onChange={event => {
                const next = [...cases];
                next[index] = { ...entry, description: event.target.value };
                setCases(next);
              }}
              placeholder="Why this branch exists — shown in the run explanation."
              value={entry.description ?? ''}
            />

            <div className="mt-2">
              {/* The same group editor the Decision node uses: a router case
                  and a business rule are the same kind of thing, so an author
                  meets the same operators and the same field picker in both. */}
              <ConditionGroupEditor
                contract={contract}
                group={entry.when ?? newGroup()}
                onChange={when => {
                  const next = [...cases];
                  next[index] = { ...entry, when };
                  setCases(next);
                }}
                operators={operators}
              />
            </div>
          </div>
        ))}
      </div>

      <button
        className="mt-2 w-full rounded border border-dashed border-slate-300 py-1.5 text-[11px] font-medium text-accent-700 hover:border-accent-600 hover:bg-accent-50"
        onClick={() => setCases([
          ...cases,
          { route: '', description: '', when: newGroup() },
        ])}
        type="button"
      >
        + Add case
      </button>
    </section>
  );
}
