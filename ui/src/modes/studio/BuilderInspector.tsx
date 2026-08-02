import type { Edge, Node } from 'reactflow';
import type { LLMModelInfo, NodeTypeManifest, WorkflowPreflightReport } from '../../api/types';
import { BuilderTestPanel } from './BuilderTestPanel';
import { ConfigPanel } from './ConfigPanel';
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

export type BuilderInspectorTab = 'configure' | 'map' | 'test' | 'guided' | 'checks';

const TABS: Array<{ id: BuilderInspectorTab; label: string }> = [
  { id: 'configure', label: 'Configure' },
  { id: 'map', label: 'Map data' },
  { id: 'test', label: 'Test' },
  { id: 'guided', label: 'Guided' },
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
  onIdChange,
  onInputsChange,
  onLaunchTest,
  onModelRoutingChange,
  onModelSelectionChange,
  onSelectNode,
  onTabChange,
  onValidate,
  preflight,
  selected,
  showInputs,
  tab,
  validating,
  workflow,
}: {
  edges: Edge<WorkflowEdgeData>[];
  llmModels: LLMModelInfo[];
  manifests: NodeTypeManifest[];
  nodes: Node<WorkflowNodeData>[];
  onClose: () => void;
  onCloseInputs: () => void;
  onConfigChange: (next: Record<string, unknown>) => void;
  onExperienceChange: (experience: NodeExperienceSpec | undefined) => void;
  onIdChange: (nextId: string) => void;
  onInputsChange: (inputs: Record<string, WorkflowInputSpec>) => void;
  onLaunchTest: (workflow: YamlWorkflow, title: string) => void;
  onModelRoutingChange: (next: ModelRoutingPolicy | undefined) => void;
  onModelSelectionChange: (next: string | null) => void;
  onSelectNode: (nodeId: string) => void;
  onTabChange: (tab: BuilderInspectorTab) => void;
  onValidate: () => void;
  preflight: WorkflowPreflightReport | null;
  selected: Node<WorkflowNodeData> | null;
  showInputs: boolean;
  tab: BuilderInspectorTab;
  validating: boolean;
  workflow: YamlWorkflow;
}) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <div className="text-xs uppercase tracking-wide text-ink-500">
          {showInputs ? 'Workflow inputs' : 'Inspector'}
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
          />
        </div>
      ) : (
        <>
          <div className="flex border-b border-slate-200" role="tablist" aria-label="Node inspector">
            {TABS.map(item => (
              <button
                aria-selected={tab === item.id}
                className={`flex-1 px-2 py-2 text-xs font-semibold transition ${
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
            {tab === 'configure' && (
              <ConfigPanel
                selected={selected}
                manifests={manifests}
                llmModels={llmModels}
                onIdChange={onIdChange}
                onConfigChange={onConfigChange}
                onModelSelectionChange={onModelSelectionChange}
                onModelRoutingChange={onModelRoutingChange}
              />
            )}
            {tab === 'map' && (
              <DataMappingPanel
                workflow={workflow}
                selected={selected}
                nodes={nodes}
                edges={edges}
                manifests={manifests}
                onConfigChange={onConfigChange}
              />
            )}
            {tab === 'test' && (
              <BuilderTestPanel
                workflow={workflow}
                selected={selected}
                edges={edges}
                nodes={nodes}
                onLaunchTest={onLaunchTest}
              />
            )}
            {tab === 'guided' && (
              <GuidedExperiencePanel
                selected={selected}
                workflow={workflow}
                onChange={onExperienceChange}
              />
            )}
            {tab === 'checks' && (
              <PreflightPanel
                report={preflight}
                validating={validating}
                onValidate={onValidate}
                onSelectNode={onSelectNode}
              />
            )}
          </div>
        </>
      )}
    </div>
  );
}
