import type { Edge, Node } from 'reactflow';
import type {
  LLMModelInfo,
  NodeTypeManifest,
  WorkflowPreflightReport,
} from '../../api/types';
import { AboutPanel } from './builder/AboutPanel';
import { ConfigureTab, useAuthoringContext } from './builder/ConfigureTab';
import { NodeTestPanel } from './builder/NodeTestPanel';
import { OutputsPanel } from './builder/OutputsPanel';
import { SimulatorPanel } from './builder/SimulatorPanel';
import { DataMappingPanel } from './DataMappingPanel';
import { GuidedExperiencePanel } from './GuidedExperiencePanel';
import { PreflightPanel } from './PreflightPanel';
import { WorkflowInputsPanel } from './WorkflowInputsPanel';
import type {
  ModelRoutingPolicy,
  NodeExperienceSpec,
  WorkflowEdgeData,
  WorkflowInputSpec,
  WorkflowNodeData,
  YamlWorkflow,
} from './yaml-bridge';

/**
 * The node inspector (§18).
 *
 * Six tabs instead of one long form, in the order an author actually works:
 * understand the step, configure it, see what reaches it, see what it
 * guarantees, test it, then the advanced knobs. Simulate sits alongside them
 * because a whole-workflow run belongs in the same place as a single-step one.
 */

export type BuilderInspectorTab =
  | 'about'
  | 'configure'
  | 'inputs'
  | 'outputs'
  | 'test'
  | 'simulate'
  | 'advanced'
  | 'checks';

const TABS: Array<{ id: BuilderInspectorTab; label: string }> = [
  { id: 'about', label: 'About' },
  { id: 'configure', label: 'Configure' },
  { id: 'inputs', label: 'Inputs' },
  { id: 'outputs', label: 'Outputs' },
  { id: 'test', label: 'Test' },
  { id: 'simulate', label: 'Simulate' },
  { id: 'advanced', label: 'Advanced' },
  { id: 'checks', label: 'Checks' },
];

export function BuilderInspector({
  edges,
  llmModels,
  manifests,
  nodes,
  onClose,
  onCloseInputs,
  onConfigChange,
  onExperienceChange,
  onHighlightPath,
  onIdChange,
  onInputsChange,
  onAutofix,
  onLaunchTest,
  onModelRoutingChange,
  onModelSelectionChange,
  onRunWorkflow,
  onSelectNode,
  onTabChange,
  onTestWorkflow,
  onValidate,
  autofixing,
  preflight,
  selected,
  showInputs,
  tab,
  validating,
  workflow,
  workflowYaml,
}: {
  edges: Edge<WorkflowEdgeData>[];
  llmModels: LLMModelInfo[];
  manifests: NodeTypeManifest[];
  nodes: Node<WorkflowNodeData>[];
  onAutofix?: () => void;
  onClose: () => void;
  onCloseInputs: () => void;
  onConfigChange: (next: Record<string, unknown>) => void;
  onExperienceChange: (experience: NodeExperienceSpec | undefined) => void;
  onHighlightPath: (path: string[], waiting: string[]) => void;
  onIdChange: (nextId: string) => void;
  onInputsChange: (inputs: Record<string, WorkflowInputSpec>) => void;
  onLaunchTest: (workflow: YamlWorkflow, title: string) => void;
  onModelRoutingChange: (next: ModelRoutingPolicy | undefined) => void;
  onModelSelectionChange: (next: string | null) => void;
  onRunWorkflow: () => void;
  onSelectNode: (nodeId: string) => void;
  onTabChange: (tab: BuilderInspectorTab) => void;
  onTestWorkflow: () => void;
  onValidate: () => void;
  autofixing?: boolean;
  preflight: WorkflowPreflightReport | null;
  selected: Node<WorkflowNodeData> | null;
  showInputs: boolean;
  tab: BuilderInspectorTab;
  validating: boolean;
  workflow: YamlWorkflow;
  workflowYaml: string;
}) {
  const manifest = selected
    ? manifests.find(item => item.type_name === selected.data.typeName)
    : undefined;

  // The typed authoring context: which operators exist, what this step can read,
  // which mailboxes are configured. Shared by the config editors so they all
  // agree with the backend and with each other.
  const { contract, emailConnections, operators } = useAuthoringContext(
    workflowYaml,
    selected?.id ?? null,
  );

  const businessLabel = selected?.data.experience?.display_name ?? '';

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <div className="min-w-0">
          <div className="text-xs uppercase tracking-wide text-ink-500">
            {showInputs ? 'Workflow inputs' : 'Inspector'}
          </div>
          {!showInputs && selected && (
            <div className="truncate text-[11px] font-semibold text-ink-900">
              {businessLabel || selected.data.nodeId}
            </div>
          )}
        </div>
        <button
          aria-label="Close inspector"
          className="text-ink-500 hover:text-ink-900"
          onClick={showInputs ? onCloseInputs : onClose}
          type="button"
        >
          ×
        </button>
      </div>

      {showInputs ? (
        <div className="min-h-0 flex-1">
          <WorkflowInputsPanel
            inputs={workflow.inputs ?? {}}
            onChange={onInputsChange}
            onClose={onCloseInputs}
            onRunWorkflow={onRunWorkflow}
            onTestWorkflow={onTestWorkflow}
            runDisabled={nodes.length === 0}
            testing={validating}
          />
        </div>
      ) : (
        <>
          <div
            aria-label="Node inspector"
            className="flex flex-wrap border-b border-slate-200"
            role="tablist"
          >
            {TABS.map(item => (
              <button
                aria-selected={tab === item.id}
                className={`flex-1 whitespace-nowrap px-2 py-2 text-[11px] font-semibold transition ${
                  tab === item.id
                    ? 'border-b-2 border-accent-600 text-accent-700'
                    : 'text-ink-500 hover:text-ink-800'
                }`}
                key={item.id}
                onClick={() => onTabChange(item.id)}
                role="tab"
                type="button"
              >
                {item.label}
              </button>
            ))}
          </div>

          <div className="min-h-0 flex-1 overflow-hidden">
            {tab === 'about' && (
              selected ? (
                <AboutPanel
                  businessLabel={businessLabel}
                  manifest={manifest}
                  nodeId={selected.data.nodeId}
                  onBusinessLabelChange={label => onExperienceChange({
                    ...(selected.data.experience ?? {}),
                    display_name: label || undefined,
                  })}
                />
              ) : (
                <EmptyState message="Select a step to see what it does." />
              )
            )}

            {tab === 'configure' && (
              selected ? (
                <ConfigureTab
                  contract={contract}
                  emailConnections={emailConnections}
                  llmModels={llmModels}
                  manifest={manifest}
                  onConfigChange={onConfigChange}
                  onIdChange={onIdChange}
                  operators={operators}
                  selected={selected}
                />
              ) : (
                <EmptyState message="Select a step to configure it, or drag one from the library." />
              )
            )}

            {tab === 'inputs' && (
              <DataMappingPanel
                contract={contract}
                edges={edges}
                manifests={manifests}
                nodes={nodes}
                onConfigChange={onConfigChange}
                selected={selected}
                workflow={workflow}
              />
            )}

            {tab === 'outputs' && (
              selected ? (
                <OutputsPanel nodeId={selected.data.nodeId} workflowYaml={workflowYaml} />
              ) : (
                <EmptyState message="Select a step to see what it guarantees to later steps." />
              )
            )}

            {tab === 'test' && (
              <NodeTestPanel
                edges={edges}
                manifest={manifest}
                onLaunchTest={onLaunchTest}
                selected={selected}
                workflow={workflow}
              />
            )}

            {tab === 'simulate' && (
              <SimulatorPanel
                onHighlightPath={onHighlightPath}
                onSelectNode={onSelectNode}
                workflow={workflow}
                workflowYaml={workflowYaml}
              />
            )}

            {tab === 'advanced' && (
              <AdvancedPanel
                llmModels={llmModels}
                manifest={manifest}
                onModelRoutingChange={onModelRoutingChange}
                onModelSelectionChange={onModelSelectionChange}
                selected={selected}
              >
                <GuidedExperiencePanel
                  onChange={onExperienceChange}
                  selected={selected}
                  workflow={workflow}
                />
              </AdvancedPanel>
            )}

            {tab === 'checks' && (
              <PreflightPanel
                autofixing={autofixing}
                onAutofix={onAutofix}
                onSelectNode={onSelectNode}
                onValidate={onValidate}
                report={preflight}
                validating={validating}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div className="p-5">
      <div className="rounded-lg border border-dashed border-ink-200 bg-brand-softer p-5 text-center text-xs leading-5 text-ink-500">
        {message}
      </div>
    </div>
  );
}

/**
 * Advanced: model routing policy and the guided-run copy.
 *
 * Kept out of Configure deliberately. Model choice and cost ceilings are real
 * settings, but putting them next to the business logic invites the reader to
 * think the model is the thing being designed. It isn't.
 */
function AdvancedPanel({
  children,
  llmModels,
  manifest,
  onModelRoutingChange,
  onModelSelectionChange,
  selected,
}: {
  children: React.ReactNode;
  llmModels: LLMModelInfo[];
  manifest: NodeTypeManifest | undefined;
  onModelRoutingChange: (next: ModelRoutingPolicy | undefined) => void;
  onModelSelectionChange: (next: string | null) => void;
  selected: Node<WorkflowNodeData> | null;
}) {
  if (!selected) {
    return <EmptyState message="Select a step to see its advanced settings." />;
  }

  const supportsModel = Boolean(
    manifest?.uses_ai
    || (manifest?.config_schema as { properties?: Record<string, unknown> })
      ?.properties?.model,
  );
  const configuredModel = typeof selected.data.config.model === 'string'
    ? selected.data.config.model
    : null;
  const selectedModel = selected.data.selectedModel ?? configuredModel;
  const routing = selected.data.modelRouting ?? {
    accuracy_priority: 'maximum' as const,
    prefer_low_latency: false,
  };

  return (
    <div className="builder-inspector-scroll p-4">
      {supportsModel && (
        <section className="rounded-lg border border-accent-200 bg-accent-50/40 p-3">
          <div className="builder-panel-heading">Model routing</div>
          <select
            className="builder-field mt-2"
            onChange={event => {
              const next = event.target.value || null;
              onModelSelectionChange(next);
              if (next === 'auto' && !selected.data.modelRouting) {
                onModelRoutingChange({
                  accuracy_priority: 'maximum',
                  prefer_low_latency: false,
                });
              }
            }}
            value={selectedModel ?? ''}
          >
            {llmModels.map(model => (
              <option key={model.name} value={model.name}>
                {model.display_name}
                {!model.configured && !model.automatic ? ' — not configured' : ''}
              </option>
            ))}
          </select>

          {selectedModel === 'auto' && (
            <div className="mt-3 space-y-2 border-t border-accent-200 pt-3">
              <p className="text-[11px] leading-4 text-ink-700">
                The router picks a model per call and records which one ran and
                why. It spends no tokens deciding.
              </p>
              <label className="block text-[11px] font-medium text-ink-700">
                Priority
                <select
                  className="builder-field mt-1"
                  onChange={event => onModelRoutingChange({
                    ...routing,
                    accuracy_priority: event.target
                      .value as ModelRoutingPolicy['accuracy_priority'],
                  })}
                  value={routing.accuracy_priority ?? 'maximum'}
                >
                  <option value="maximum">Maximum quality</option>
                  <option value="balanced">Balanced quality and cost</option>
                  <option value="economy">Economy</option>
                </select>
              </label>
              <label className="block text-[11px] font-medium text-ink-700">
                Cost ceiling per call (USD)
                <input
                  className="builder-field mt-1"
                  min="0"
                  onChange={event => onModelRoutingChange({
                    ...routing,
                    max_estimated_cost_usd: event.target.value === ''
                      ? null
                      : Number(event.target.value),
                  })}
                  placeholder="No ceiling"
                  step="0.001"
                  type="number"
                  value={routing.max_estimated_cost_usd ?? ''}
                />
              </label>
              <label className="flex items-start gap-2 text-[11px] text-ink-700">
                <input
                  checked={Boolean(routing.prefer_low_latency)}
                  className="mt-0.5"
                  onChange={event => onModelRoutingChange({
                    ...routing,
                    prefer_low_latency: event.target.checked,
                  })}
                  type="checkbox"
                />
                Prefer lower latency when quality is comparable
              </label>
            </div>
          )}
        </section>
      )}

      <div className="mt-4">{children}</div>
    </div>
  );
}
