import type { NodeTypeManifest } from '../../api/types';
import { SchemaForm } from './SchemaForm';
import { findManifest } from './builder-helpers';
import type { WorkflowNodeData } from './yaml-bridge';

export function ConfigPanel({
  selected,
  manifests,
  onIdChange,
  onConfigChange,
}: {
  selected: { id: string; data: WorkflowNodeData } | null;
  manifests: NodeTypeManifest[];
  onIdChange: (nextId: string) => void;
  onConfigChange: (next: Record<string, unknown>) => void;
}) {
  if (!selected) {
    return (
      <div className="p-6 text-ink-500 text-sm">
        Click a node to edit its config. Drag a node from the left palette to add one.
      </div>
    );
  }
  const manifest = findManifest(manifests, selected.data.typeName);
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
      <h3 className="text-sm font-medium text-ink-700 mt-6 mb-2">Config</h3>
      {manifest ? (
        <SchemaForm
          schema={manifest.config_schema}
          value={selected.data.config}
          onChange={onConfigChange}
        />
      ) : (
        <div className="text-sm text-bad">
          No manifest for type {selected.data.typeName}.
        </div>
      )}
    </div>
  );
}