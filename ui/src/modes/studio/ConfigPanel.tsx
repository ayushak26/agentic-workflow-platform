import type { NodeTypeManifest } from '../../api/types';
import { SchemaForm } from './SchemaForm';
import { findManifest } from './builder-helpers';
import type { WorkflowNodeData } from './yaml-bridge';

type JsonSchema = {
  type?: string;
  enum?: string[];
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  properties?: Record<string, JsonSchema>;
  'x-enum-labels'?: Record<string, string>;
};

function modelPickerSchema(
  schema: Record<string, unknown> | undefined,
): JsonSchema | null {
  const root = schema as JsonSchema | undefined;
  const model = root?.properties?.model;
  if (!model) return null;
  if (model.enum) return model;
  return (
    [...(model.anyOf ?? []), ...(model.oneOf ?? [])]
      .find(candidate => Array.isArray(candidate.enum))
    ?? null
  );
}

export function ConfigPanel({
  selected,
  manifests,
  onIdChange,
  onConfigChange,
  onModelSettingsChange,
}: {
  selected: { id: string; data: WorkflowNodeData } | null;
  manifests: NodeTypeManifest[];
  onIdChange: (nextId: string) => void;
  onConfigChange: (next: Record<string, unknown>) => void;
  onModelSettingsChange: (
    selectedModel: string | null,
    modelRouting: WorkflowNodeData['modelRouting'],
  ) => void;
}) {
  if (!selected) {
    return (
      <div className="p-6 text-ink-500 text-sm">
        Click a node to edit its config. Drag a node from the left palette to add one.
      </div>
    );
  }
  const manifest = findManifest(manifests, selected.data.typeName);
  const modelSchema = modelPickerSchema(manifest?.config_schema);
  const allModelOptions = modelSchema?.enum ?? [];
  const allowed = selected.data.allowedModels;
  const modelOptions = allModelOptions.filter(
    model => model === 'auto' || !allowed || allowed.includes(model),
  );
  const selectedModel = selected.data.selectedModel ?? '';
  const routing = selected.data.modelRouting ?? {
    accuracy_priority: 'balanced' as const,
    prefer_low_latency: false,
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
      {modelSchema && (
        <div className="mt-6 rounded-lg border border-accent-200 bg-accent-50/50 p-3">
          <h3 className="text-sm font-medium text-ink-700">
            Model selection
          </h3>
          <label className="block text-xs font-medium text-ink-700 mt-3">
            LLM for this node
          </label>
          <select
            value={selectedModel}
            onChange={event => {
              onModelSettingsChange(
                event.target.value || null,
                routing,
              );
            }}
            className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border bg-white"
          >
            <option value="">
              Use node config ({String(selected.data.config.model ?? 'default')})
            </option>
            {modelOptions.map(model => (
              <option key={model} value={model}>
                {modelSchema['x-enum-labels']?.[model] ?? model}
              </option>
            ))}
          </select>
          <p className="text-xs text-ink-500 mt-1">
            Automatic routing uses no extra LLM call. It evaluates the actual
            prompt, task complexity, configured providers, cost, and offline
            quality scores.
          </p>

          {selectedModel === 'auto' && (
            <div className="mt-3 space-y-3 border-t border-accent-200 pt-3">
              <div>
                <label className="block text-xs font-medium text-ink-700">
                  Accuracy priority
                </label>
                <select
                  value={routing.accuracy_priority ?? 'balanced'}
                  onChange={event => onModelSettingsChange(
                    'auto',
                    {
                      ...routing,
                      accuracy_priority: event.target.value as
                        | 'maximum'
                        | 'balanced'
                        | 'economy',
                    },
                  )}
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border bg-white"
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
                  value={routing.max_estimated_cost_usd ?? ''}
                  placeholder="No per-call ceiling"
                  onChange={event => onModelSettingsChange(
                    'auto',
                    {
                      ...routing,
                      max_estimated_cost_usd:
                        event.target.value === ''
                          ? null
                          : Number(event.target.value),
                    },
                  )}
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-1.5 px-2 border bg-white"
                />
              </div>
              <label className="flex items-start gap-2 text-xs text-ink-700">
                <input
                  type="checkbox"
                  checked={routing.prefer_low_latency ?? false}
                  onChange={event => onModelSettingsChange(
                    'auto',
                    {
                      ...routing,
                      prefer_low_latency: event.target.checked,
                    },
                  )}
                  className="mt-0.5"
                />
                Prefer faster models when quality is otherwise close
              </label>
              {allowed && (
                <p className="text-[11px] text-ink-500">
                  Candidates: {allowed.join(', ')}
                </p>
              )}
            </div>
          )}
        </div>
      )}
      <h3 className="text-sm font-medium text-ink-700 mt-6 mb-2">Config</h3>
      {manifest ? (
        <SchemaForm
          schema={manifest.config_schema}
          value={selected.data.config}
          onChange={onConfigChange}
          hiddenFields={modelSchema ? ['model'] : []}
        />
      ) : (
        <div className="text-sm text-bad">
          No manifest for type {selected.data.typeName}.
        </div>
      )}
    </div>
  );
}
