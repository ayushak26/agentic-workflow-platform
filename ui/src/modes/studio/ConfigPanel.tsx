import type { LLMModelInfo, NodeTypeManifest } from '../../api/types';
import { ModelSelect } from './ModelSelect';
import { SchemaForm } from './SchemaForm';
import { findManifest } from './builder-helpers';
import type {
  ModelRoutingPolicy,
  WorkflowNodeData,
} from './yaml-bridge';

export function ConfigPanel({
  selected,
  manifests,
  llmModels,
  onIdChange,
  onConfigChange,
  onModelSelectionChange,
  onModelRoutingChange,
}: {
  selected: { id: string; data: WorkflowNodeData } | null;
  manifests: NodeTypeManifest[];
  llmModels: LLMModelInfo[];
  onIdChange: (nextId: string) => void;
  onConfigChange: (next: Record<string, unknown>) => void;
  onModelSelectionChange: (next: string | null) => void;
  onModelRoutingChange: (next: ModelRoutingPolicy | undefined) => void;
}) {
  if (!selected) {
    return (
      <div className="p-6 text-ink-500 text-sm">
        Click a node to edit its config. Drag a node from the left palette to add one.
      </div>
    );
  }
  const manifest = findManifest(manifests, selected.data.typeName);
  const supportsModelSelection = Boolean(
    manifest?.config_schema
      && typeof manifest.config_schema === 'object'
      && 'properties' in manifest.config_schema
      && (
        manifest.config_schema as {
          properties?: Record<string, unknown>;
        }
      ).properties?.model,
  );
  const configuredModel = typeof selected.data.config.model === 'string'
    ? selected.data.config.model
    : null;
  const selectedModel = selected.data.selectedModel ?? configuredModel;
  const modelInfo = llmModels.find(item => item.name === selectedModel);
  const routingPolicy = selected.data.modelRouting ?? {
    accuracy_priority: 'maximum',
    prefer_low_latency: false,
  };

  const updateRoutingPolicy = (
    patch: Partial<ModelRoutingPolicy>,
  ) => {
    onModelRoutingChange({
      ...routingPolicy,
      ...patch,
    });
  };

  return (
    <div className="p-6 overflow-y-auto h-full">
      <div className="text-xs uppercase tracking-wide text-ink-500">
        {selected.data.typeName}
      </div>
      <div className="mt-2">
        <label className="block text-xs font-medium text-ink-700">Node id</label>
        <input
          type="text"
          value={selected.data.nodeId}
          onChange={e => onIdChange(e.target.value)}
          className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border font-mono"
        />
        <p className="text-xs text-ink-500 mt-1">
          Other nodes reference this id in templates like {'{{node_id.field}}'}.
        </p>
      </div>
      {supportsModelSelection && (
        <section className="mt-6 rounded-lg border border-accent-200 bg-accent-50/40 p-4">
          <label className="block text-xs font-semibold uppercase tracking-wide text-accent-700">
            LLM / model
          </label>
          <ModelSelect
            value={selectedModel ?? ''}
            llmModels={llmModels}
            onChange={next => {
              onModelSelectionChange(next || null);
              if (next === 'auto' && !selected.data.modelRouting) {
                onModelRoutingChange({
                  accuracy_priority: 'maximum',
                  prefer_low_latency: false,
                });
              }
            }}
            className="mt-2"
          />

          {selectedModel === 'auto' && (
            <div className="mt-3 space-y-3 border-t border-accent-200 pt-3">
              <p className="text-xs leading-5 text-ink-700">
                The router inspects the real task at call time, removes
                unavailable models, and records which model ran and why.
              </p>
              {modelInfo?.automatic && !modelInfo.configured && (
                <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
                  No eligible LLM provider is configured. Add a cloud API key
                  or enable a private Kimi/GLM endpoint before running.
                </div>
              )}
              <div>
                <label className="block text-xs font-medium text-ink-700">
                  Accuracy priority
                </label>
                <select
                  value={routingPolicy.accuracy_priority ?? 'maximum'}
                  onChange={event => updateRoutingPolicy({
                    accuracy_priority: event.target.value as
                      ModelRoutingPolicy['accuracy_priority'],
                  })}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                >
                  <option value="maximum">Maximum quality</option>
                  <option value="balanced">Balanced quality and cost</option>
                  <option value="economy">Economy</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-ink-700">
                  Maximum estimated cost per call (USD)
                </label>
                <input
                  type="number"
                  min="0"
                  step="0.001"
                  placeholder="No ceiling"
                  value={routingPolicy.max_estimated_cost_usd ?? ''}
                  onChange={event => updateRoutingPolicy({
                    max_estimated_cost_usd: event.target.value === ''
                      ? null
                      : Number(event.target.value),
                  })}
                  className="mt-1 block w-full rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
                />
              </div>
              <label className="flex items-start gap-2 text-xs text-ink-700">
                <input
                  type="checkbox"
                  checked={Boolean(routingPolicy.prefer_low_latency)}
                  onChange={event => updateRoutingPolicy({
                    prefer_low_latency: event.target.checked,
                  })}
                  className="mt-0.5"
                />
                Prefer lower latency when eligible models have similar quality
              </label>
            </div>
          )}
        </section>
      )}
      <h3 className="text-sm font-medium text-ink-700 mt-6 mb-2">Config</h3>
      {modelInfo?.local && (
        <div
          className={`mb-4 rounded-md border p-3 text-xs ${
            modelInfo.enabled && modelInfo.configured
              ? 'border-emerald-200 bg-emerald-50 text-emerald-800'
              : 'border-amber-200 bg-amber-50 text-amber-800'
          }`}
        >
          <div className="font-semibold">{modelInfo.display_name}</div>
          <div className="mt-1">
            {modelInfo.enabled && modelInfo.configured
              ? 'Private endpoint configured. Preflight will verify it before the run.'
              : 'Local endpoint is disabled or incomplete. Configure it in the deployment environment and restart the API.'}
          </div>
          <div className="mt-1">
            Provider: {modelInfo.provider} · API-metered cost: $0
          </div>
        </div>
      )}
      {manifest ? (
        <SchemaForm
          schema={manifest.config_schema}
          value={selected.data.config}
          onChange={onConfigChange}
          hiddenFields={supportsModelSelection ? ['model'] : []}
          typeName={selected.data.typeName}
        />
      ) : (
        <div className="text-sm text-bad">
          No manifest for type {selected.data.typeName}.
        </div>
      )}
    </div>
  );
}
