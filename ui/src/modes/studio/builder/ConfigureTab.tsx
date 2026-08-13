import { useEffect, useState } from 'react';

import { api } from '../../../api/client';
import type {
  BusinessRule,
  EmailConnectionInfo,
  FieldSpec,
  LLMModelInfo,
  NodeTypeManifest,
  OperatorCatalog,
  OutputContract,
} from '../../../api/types';
import { SchemaForm } from '../SchemaForm';
import type { WorkflowNodeData } from '../yaml-bridge';
import { AITaskConfig } from './AITaskConfig';
import { EmailConfig } from './EmailConfig';
import { MCPToolConfig } from './MCPToolConfig';
import { RouterEditor } from './RouterEditor';
import { RuleBuilder } from './RuleBuilder';

/**
 * The Configure tab.
 *
 * Core primitives get a purpose-built editor: a schema builder for the AI Task,
 * a rule editor for Decision, a branch table for Router. Everything else falls
 * back to the generic schema-driven form, which is what keeps the 43 existing
 * specialized node types working untouched.
 *
 * A node type earns a custom editor when its configuration is the *product* —
 * when a generic form would technically work but would make the thing this
 * platform is for (authoring business logic visually) unpleasant enough that
 * nobody would do it.
 */

export function ConfigureTab({
  contract,
  emailConnections,
  llmModels,
  manifest,
  onConfigChange,
  onIdChange,
  operators,
  selected,
}: {
  contract: OutputContract | null;
  emailConnections: EmailConnectionInfo[];
  llmModels: LLMModelInfo[];
  manifest: NodeTypeManifest | undefined;
  onConfigChange: (next: Record<string, unknown>) => void;
  onIdChange: (nextId: string) => void;
  operators: OperatorCatalog | null;
  selected: { id: string; data: WorkflowNodeData };
}) {
  const config = selected.data.config;
  const typeName = selected.data.typeName;

  return (
    <div className="builder-inspector-scroll p-4">
      <label className="block text-[11px] font-medium text-ink-700">
        Step id
        <input
          className="builder-field mt-1 font-mono"
          onChange={event => onIdChange(event.target.value)}
          value={selected.data.nodeId}
        />
      </label>
      <p className="mt-1 text-[10px] text-ink-500">
        How other steps address this one. Renaming it updates every reference
        automatically.
      </p>

      <div className="mt-4">
        {typeName === 'AITaskAgent' && (
          <AITaskConfig
            config={config}
            contract={contract}
            llmModels={llmModels}
            onChange={onConfigChange}
            presets={manifest?.presets ?? []}
          />
        )}

        {typeName === 'DecisionAgent' && (
          <DecisionConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
            operators={operators}
            presets={manifest?.presets ?? []}
          />
        )}

        {typeName === 'RouterAgent' && (
          <RouterEditor
            config={config}
            contract={contract}
            onChange={onConfigChange}
            operators={operators}
          />
        )}

        {typeName === 'EmailAgent' && (
          <EmailConfig
            config={config}
            connections={emailConnections}
            contract={contract}
            onChange={onConfigChange}
            presets={manifest?.presets ?? []}
          />
        )}

        {typeName === 'MCPToolAgent' && (
          <MCPToolConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {!['AITaskAgent', 'DecisionAgent', 'RouterAgent', 'EmailAgent', 'MCPToolAgent'].includes(typeName)
          && (manifest ? (
            <SchemaForm
              onChange={onConfigChange}
              schema={manifest.config_schema}
              typeName={typeName}
              value={config}
            />
          ) : (
            <div className="text-sm text-bad">No manifest for type {typeName}.</div>
          ))}
      </div>
    </div>
  );
}

/**
 * Decision configuration: default values, then the rules.
 *
 * The defaults matter more than they look. They are the node's contract: a
 * downstream reference to `decisions.human_review` resolves on every run,
 * including the ones where no escalation rule fired. Without them, the
 * "nothing went wrong" path is the one that breaks.
 */
function DecisionConfig({
  config,
  contract,
  onChange,
  operators,
  presets,
}: {
  config: Record<string, unknown>;
  contract: OutputContract | null;
  onChange: (next: Record<string, unknown>) => void;
  operators: OperatorCatalog | null;
  presets: NodeTypeManifest['presets'];
}) {
  const rules = (config.rules as BusinessRule[] | undefined) ?? [];
  const defaults = (config.defaults as Record<string, unknown> | undefined) ?? {};

  return (
    <div>
      {presets.length > 0 && (
        <section>
          <div className="builder-panel-heading">Start from</div>
          <div className="mt-2 grid grid-cols-2 gap-1.5">
            {presets.map(preset => (
              <button
                className="rounded-md border border-slate-200 p-2 text-left transition hover:border-accent-400"
                key={preset.id}
                onClick={() => onChange({
                  ...config,
                  rules: [...rules, ...((preset.rules as BusinessRule[]) ?? [])],
                })}
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
        <div className="builder-panel-heading">Starting values</div>
        <p className="mt-1 text-[11px] leading-4 text-ink-500">
          What each conclusion is before any rule runs. These make the step&apos;s
          output complete: a later step reading{' '}
          <span className="font-mono">human_review</span> gets{' '}
          <span className="font-mono">false</span> rather than nothing on the
          runs where no rule fired.
        </p>
        <DefaultsEditor
          defaults={defaults}
          onChange={next => onChange({ ...config, defaults: next })}
        />
      </section>

      <RuleBuilder
        contract={contract}
        onChange={next => onChange({ ...config, rules: next })}
        operators={operators}
        rules={rules}
      />
    </div>
  );
}

function DefaultsEditor({
  defaults,
  onChange,
}: {
  defaults: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
}) {
  const entries = Object.entries(defaults);

  const rename = (from: string, to: string) => {
    const next: Record<string, unknown> = {};
    for (const [key, value] of entries) next[key === from ? to : key] = value;
    onChange(next);
  };

  return (
    <div className="mt-2 space-y-1.5">
      {entries.map(([key, value]) => (
        <div className="flex items-center gap-2" key={key}>
          <input
            aria-label="Field name"
            className="builder-field flex-1 font-mono"
            onChange={event => rename(key, event.target.value)}
            value={key}
          />
          <span className="flex-none text-ink-400">=</span>
          <input
            aria-label={`Default value for ${key}`}
            className="builder-field w-40 flex-none"
            onChange={event => {
              const raw = event.target.value;
              const parsed = raw === 'true'
                ? true
                : raw === 'false'
                  ? false
                  : raw !== '' && !Number.isNaN(Number(raw))
                    ? Number(raw)
                    : raw;
              onChange({ ...defaults, [key]: parsed });
            }}
            value={String(value ?? '')}
          />
          <button
            aria-label={`Remove ${key}`}
            className="flex-none px-1 text-ink-400 hover:text-red-600"
            onClick={() => {
              const next = { ...defaults };
              delete next[key];
              onChange(next);
            }}
            type="button"
          >×</button>
        </div>
      ))}
      <button
        className="text-[11px] font-medium text-accent-700 hover:underline"
        onClick={() => onChange({ ...defaults, ['new_field']: false })}
        type="button"
      >
        + Add starting value
      </button>
    </div>
  );
}

/** Fetches the shared authoring context once per Builder session.
 *  The operator catalog never changes during editing; the output contract does,
 *  so it is refetched whenever the workflow or selected step changes. */
export function useAuthoringContext(workflowYaml: string, nodeId: string | null) {
  const [operators, setOperators] = useState<OperatorCatalog | null>(null);
  const [contract, setContract] = useState<OutputContract | null>(null);
  const [emailConnections, setEmailConnections] = useState<EmailConnectionInfo[]>([]);

  useEffect(() => {
    api.operatorCatalog().then(setOperators).catch(() => setOperators(null));
    api.emailConnections()
      .then(result => setEmailConnections(result.connections))
      .catch(() => setEmailConnections([]));
  }, []);

  useEffect(() => {
    if (!nodeId || !workflowYaml) {
      setContract(null);
      return;
    }
    let cancelled = false;
    // Debounced: the Builder rewrites the YAML on every keystroke, and the
    // contract only changes when the graph or a schema does.
    const timer = window.setTimeout(() => {
      api.outputContract(workflowYaml, nodeId)
        .then(result => { if (!cancelled) setContract(result); })
        .catch(() => { if (!cancelled) setContract(null); });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [nodeId, workflowYaml]);

  return { contract, emailConnections, operators };
}

export type { FieldSpec };
