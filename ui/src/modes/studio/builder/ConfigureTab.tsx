import { useCallback, useEffect, useState } from 'react';

import { api } from '../../../api/client';
import type {
  BusinessRule,
  EmailConnectionInfo,
  FieldSpec,
  IntegrationConnectionInfo,
  LLMModelInfo,
  NodeTypeManifest,
  OperatorCatalog,
  OutputContract,
} from '../../../api/types';
import { SchemaForm } from '../SchemaForm';
import type { WorkflowInputSpec, WorkflowNodeData, YamlWorkflow } from '../yaml-bridge';
import { AITaskConfig } from './AITaskConfig';
import { DataTransformConfig } from './DataTransformConfig';
import { EmailConfig } from './EmailConfig';
import { EndAgentConfig } from './EndAgentConfig';
import { ExternalActionConfig } from './ExternalActionConfig';
import { IntegrationConfig } from './IntegrationConfig';
import { JoinConfig } from './JoinConfig';
import { MCPToolConfig } from './MCPToolConfig';
import { PromptTemplateConfig } from './PromptTemplateConfig';
import { PythonSnippetConfig } from './PythonSnippetConfig';
import { RAGAgentConfig } from './RAGAgentConfig';
import { ModeCard, RouterEditor } from './RouterEditor';
import { RuleBuilder } from './RuleBuilder';
import { StartAgentConfig } from './StartAgentConfig';
import { WorkflowInputAgentConfig } from './WorkflowInputAgentConfig';
import { SQLQueryConfig } from './SQLQueryConfig';

function asNonEmptyString(value: unknown): boolean {
  return typeof value === 'string' && value.trim().length > 0;
}

/** A TransformAgent node authored the old way (hand-written prompt_template/
 *  system_prompt/output_schema) keeps rendering through the generic
 *  SchemaForm, unchanged — only a brand-new node gets the Inputs/
 *  Instructions/Outputs editor. Mirrors the backend's `is_new_style` check
 *  (app/nodes/transform.py) so a node never shows a blank editor over data
 *  that's actually there. */
function isLegacyTransform(config: Record<string, unknown>): boolean {
  return !asNonEmptyString(config.instructions)
    && (
      asNonEmptyString(config.prompt_template)
      || asNonEmptyString(config.system_prompt)
      || Object.keys((config.output_schema as Record<string, unknown> | undefined) ?? {}).length > 0
    );
}

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
  integrationConnections,
  llmModels,
  manifest,
  onConfigChange,
  onIdChange,
  onInputsChange,
  operators,
  refetchEmailConnections,
  refetchIntegrationConnections,
  selected,
  workflow,
}: {
  contract: OutputContract | null;
  emailConnections: EmailConnectionInfo[];
  integrationConnections: IntegrationConnectionInfo[];
  llmModels: LLMModelInfo[];
  manifest: NodeTypeManifest | undefined;
  onConfigChange: (next: Record<string, unknown>) => void;
  onIdChange: (nextId: string) => void;
  onInputsChange: (inputs: Record<string, WorkflowInputSpec>) => void;
  operators: OperatorCatalog | null;
  refetchEmailConnections: () => void;
  refetchIntegrationConnections: () => void;
  selected: { id: string; data: WorkflowNodeData };
  workflow: YamlWorkflow;
}) {
  const config = selected.data.config;
  const typeName = selected.data.typeName;
  // TransformAgent converges AI work and DataTransformAgent's deterministic
  // work on one node type — `mode` picks which. Absent `mode` (every node
  // saved before this converged) behaves exactly as before: "ai".
  const transformMode = typeName === 'TransformAgent'
    ? (config.mode === 'deterministic' ? 'deterministic' : 'ai')
    : null;
  const useNewPromptTemplateEditor = transformMode === 'ai' && !isLegacyTransform(config);

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
          <>
            <div className="mb-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
              AITaskAgent is deprecated — TransformAgent's Inputs/Instructions/
              Outputs editor now covers the same ground plus a fail_on_error
              escape hatch. Use TransformAgent for new steps.
            </div>
            <AITaskConfig
              config={config}
              contract={contract}
              llmModels={llmModels}
              onChange={onConfigChange}
              presets={manifest?.presets ?? []}
              typeName={typeName}
            />
          </>
        )}

        {typeName === 'WorkflowInputAgent' && (
          <WorkflowInputAgentConfig
            config={config}
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
            onConnectionsChanged={refetchEmailConnections}
            presets={manifest?.presets ?? []}
          />
        )}

        {typeName === 'IntegrationAgent' && (
          <IntegrationConfig
            config={config}
            connections={integrationConnections}
            contract={contract}
            onChange={onConfigChange}
            onConnectionsChanged={refetchIntegrationConnections}
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

        {typeName === 'ExternalActionAgent' && (
          <ExternalActionConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'PythonSnippetAgent' && (
          <PythonSnippetConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'SQLQueryAgent' && (
          <SQLQueryConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'RAGAgent' && (
          <RAGAgentConfig
            config={config}
            contract={contract}
            llmModels={llmModels}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'StartAgent' && (
          <StartAgentConfig
            config={config}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'EndAgent' && (
          <EndAgentConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'TextAssemblerAgent' && (
          <JoinConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {typeName === 'DataTransformAgent' && (
          <>
            <div className="mb-2 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] text-amber-900">
              DataTransformAgent is deprecated — TransformAgent's Deterministic
              mode now covers the same operations. Use TransformAgent for new
              steps.
            </div>
            <DataTransformConfig
              config={config}
              contract={contract}
              onChange={onConfigChange}
            />
          </>
        )}

        {typeName === 'TransformAgent' && (
          <div className="mb-3 grid grid-cols-2 gap-1.5">
            <ModeCard
              active={transformMode === 'ai'}
              description="Send content to a model — extract, classify, summarize, draft."
              label="AI"
              onSelect={() => onConfigChange({ ...config, mode: 'ai' })}
            />
            <ModeCard
              active={transformMode === 'deterministic'}
              description="Reshape data with no model call — copy, format, join, coalesce."
              label="Deterministic"
              onSelect={() => onConfigChange({ ...config, mode: 'deterministic' })}
            />
          </div>
        )}

        {transformMode === 'deterministic' && (
          <DataTransformConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
          />
        )}

        {useNewPromptTemplateEditor && (
          <PromptTemplateConfig
            config={config}
            contract={contract}
            onChange={onConfigChange}
            onWorkflowInputsChange={onInputsChange}
            workflowInputs={workflow.inputs ?? {}}
          />
        )}

        {!['AITaskAgent', 'DecisionAgent', 'RouterAgent', 'EmailAgent', 'IntegrationAgent', 'MCPToolAgent', 'DataTransformAgent', 'TextAssemblerAgent', 'ExternalActionAgent', 'PythonSnippetAgent', 'SQLQueryAgent', 'WorkflowInputAgent', 'RAGAgent', 'StartAgent', 'EndAgent'].includes(typeName)
          && !useNewPromptTemplateEditor
          && transformMode !== 'deterministic'
          && (manifest ? (
            <SchemaForm
              hiddenFields={
                // input_fields/instructions/output_fields only exist for the
                // new Inputs/Instructions/Outputs editor (PromptTemplateConfig);
                // mode/operations/omit_empty only exist for Deterministic mode
                // (DataTransformConfig, rendered separately above) — a legacy
                // AI-mode TransformAgent node uses neither, so don't clutter
                // its raw editor with empty boxes for fields it doesn't have.
                typeName === 'TransformAgent'
                  ? ['input_fields', 'instructions', 'output_fields', 'mode', 'operations', 'omit_empty']
                  : []
              }
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

export function DefaultsEditor({
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
      {entries.map(([key, value], index) => (
        <div className="flex items-center gap-2" key={index}>
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
  const [integrationConnections, setIntegrationConnections] = useState<IntegrationConnectionInfo[]>([]);

  const refetchEmailConnections = useCallback(() => {
    api.emailConnections()
      .then(result => setEmailConnections(result.connections))
      .catch(() => setEmailConnections([]));
  }, []);

  const refetchIntegrationConnections = useCallback(() => {
    api.integrationConnections()
      .then(result => setIntegrationConnections(result.connections))
      .catch(() => setIntegrationConnections([]));
  }, []);

  useEffect(() => {
    api.operatorCatalog().then(setOperators).catch(() => setOperators(null));
    refetchEmailConnections();
    refetchIntegrationConnections();
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  return {
    contract,
    emailConnections,
    integrationConnections,
    operators,
    refetchEmailConnections,
    refetchIntegrationConnections,
  };
}

export type { FieldSpec };
