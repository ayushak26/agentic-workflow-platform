import { useMemo, useState } from 'react';
import type { Edge, Node } from 'reactflow';
import type { ContractField, NodeTypeManifest, OutputContract } from '../../api/types';
import { resolveBinding } from './builder/binding';
import { ValuePicker } from './builder/FieldPicker';
import { InfoPopover } from './builder/InfoPopover';
import type {
  WorkflowEdgeData,
  WorkflowNodeData,
  YamlWorkflow,
} from './yaml-bridge';

/**
 * The Inputs tab — where a step's fields get their values.
 *
 * The interaction is always "for this field, use this value from an earlier
 * step" (or type one directly) — every configurable field is listed as its
 * own card immediately, connected ones showing what they're connected to and
 * unconfigured ones offering the two ways to set a value. Nothing here shows
 * `{{...}}` syntax by default; "View reference" reveals it for anyone who
 * wants it.
 */

type PropertySchema = {
  type?: string;
  anyOf?: Array<{ type?: string }>;
  oneOf?: Array<{ type?: string }>;
  title?: string;
  description?: string;
};

function acceptsText(schema: PropertySchema): boolean {
  if (schema.type === 'string') return true;
  return [...(schema.anyOf ?? []), ...(schema.oneOf ?? [])]
    .some(option => option.type === 'string');
}

// Fields that already have a dedicated, constrained control on the Configure
// tab (a discovered-tool grid, a mode switch, a preset picker, a connection
// dropdown) — offering them again here as free-typed-or-mapped text would let
// an author write a value the guided control would never allow, e.g. mapping
// MCPToolAgent's `tool` to an upstream string that isn't a real tool name.
// Mirrors ConfigureTab.tsx's `hiddenFields` pattern for the generic SchemaForm
// fallback, applied here for the same reason.
const CONFIGURE_TAB_OWNED_FIELDS: Record<string, string[]> = {
  MCPToolAgent: ['server_id', 'tool'],
  RouterAgent: ['mode'],
  AITaskAgent: ['task', 'model'],
  EmailAgent: ['connection', 'operation'],
};

function humanLabel(schema: PropertySchema, name: string): string {
  return schema.title || name.replace(/_/g, ' ');
}

function leafLabel(field: ContractField): string {
  const leaf = field.path.split('.').slice(-1)[0] || field.path;
  return leaf.replace(/_/g, ' ').replace(/^./, char => char.toUpperCase());
}

export function DataMappingPanel({
  contract,
  edges,
  manifests,
  nodes,
  onConfigChange,
  previewValues,
  selected,
}: {
  contract: OutputContract | null;
  edges: Edge<WorkflowEdgeData>[];
  manifests: NodeTypeManifest[];
  nodes: Node<WorkflowNodeData>[];
  onConfigChange: (next: Record<string, unknown>) => void;
  previewValues?: Record<string, Record<string, string>>;
  selected: Node<WorkflowNodeData> | null;
  workflow: Pick<YamlWorkflow, 'inputs' | 'static_variables'>;
}) {
  const manifest = selected
    ? manifests.find(item => item.type_name === selected.data.typeName)
    : undefined;
  const properties = (
    manifest?.config_schema as { properties?: Record<string, PropertySchema> }
  )?.properties ?? {};
  const ownedFields = selected ? CONFIGURE_TAB_OWNED_FIELDS[selected.data.typeName] ?? [] : [];
  const targetFields = Object.entries(properties)
    .filter(([name, schema]) => acceptsText(schema) && !ownedFields.includes(name))
    .map(([name, schema]) => ({
      name,
      label: humanLabel(schema, name),
      description: schema.description ?? '',
    }));

  const downstream = useMemo(() => (
    selected
      ? edges
        .filter(edge => edge.source === selected.id)
        .map(edge => nodes.find(node => node.id === edge.target)?.data.nodeId ?? edge.target)
      : []
  ), [edges, nodes, selected]);

  if (!selected) {
    return (
      <EmptyMappingState
        title="Select a step to set up its inputs"
        description="Choose which value each field should use — the picker only shows values that can actually reach the selected step."
      />
    );
  }

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading">Inputs</div>
      <p className="mt-1 flex flex-wrap items-center gap-1 text-[11px] leading-4 text-ink-500">
        Choose which input you want to fill, then select the value you want to use.
        <InfoPopover feature="data_mapping" label="How does this work?" />
      </p>

      {targetFields.length === 0 ? (
        <div className="mt-4 rounded-md border border-dashed border-slate-300 p-3 text-center text-[11px] text-ink-500">
          {ownedFields.length > 0
            ? 'This step’s fields are set on the Configure tab, each with its own picker — for example, an MCP Tool’s arguments only exist once you’ve chosen a tool there.'
            : 'This step has no configurable text fields.'}
        </div>
      ) : (
        <div className="mt-4 space-y-3">
          {targetFields.map(target => (
            <FieldCard
              config={selected.data.config}
              contract={contract}
              key={target.name}
              onConfigChange={onConfigChange}
              previewValues={previewValues}
              target={target}
            />
          ))}
        </div>
      )}

      <details className="mt-5 rounded-lg border border-ink-100 bg-brand-softer p-3">
        <summary className="cursor-pointer text-[11px] font-semibold text-ink-700">
          This step
        </summary>
        <section className="builder-mapping-card mt-3">
          <div className="builder-mapping-node">
            <span className="builder-mapping-port builder-mapping-port--in" />
            <div className="text-[10px] uppercase tracking-wide text-accent-700">
              This step
            </div>
            <div className="mt-1 truncate text-xs font-semibold text-ink-900">
              {selected.data.experience?.display_name || selected.data.nodeId}
            </div>
            <div className="mt-0.5 truncate font-mono text-[10px] text-ink-500">
              {selected.data.nodeId}
            </div>
            <span className="builder-mapping-port builder-mapping-port--out" />
          </div>
          <div className="mt-3 text-[11px] text-ink-500">
            Feeds {downstream.length > 0 ? downstream.join(', ') : 'the workflow output'}.
          </div>
        </section>
      </details>
    </div>
  );
}

function FieldCard({
  config,
  contract,
  onConfigChange,
  previewValues,
  target,
}: {
  config: Record<string, unknown>;
  contract: OutputContract | null;
  onConfigChange: (next: Record<string, unknown>) => void;
  previewValues?: Record<string, Record<string, string>>;
  target: { name: string; label: string; description: string };
}) {
  const [mode, setMode] = useState<'idle' | 'enter' | 'picker'>('idle');
  const binding = useMemo(
    () => resolveBinding(config[target.name], contract),
    [config, contract, target.name],
  );

  const setValue = (next: string) => {
    onConfigChange({ ...config, [target.name]: next });
  };
  const remove = () => {
    onConfigChange(
      Object.fromEntries(Object.entries(config).filter(([key]) => key !== target.name)),
    );
    setMode('idle');
  };

  const connected = binding.kind === 'resolved' || binding.kind === 'unresolved';

  return (
    <div className="rounded-lg border border-ink-100 bg-white p-3">
      <div className="text-[12px] font-semibold text-ink-900">{target.label}</div>

      {binding.kind === 'resolved' && (
        <ConnectedRow
          currentReference={typeof config[target.name] === 'string' ? config[target.name] as string : ''}
          field={binding.field}
          onRemove={remove}
          onStartChange={() => setMode('picker')}
          preview={previewValues?.[binding.node?.node_id ?? '']?.[binding.field.path] ?? (binding.field.description || null)}
          previewIsLive={Boolean(previewValues?.[binding.node?.node_id ?? '']?.[binding.field.path])}
          stepLabel={binding.node ? binding.node.label : 'Workflow Input'}
        />
      )}

      {binding.kind === 'unresolved' && (
        <div className="mt-1.5">
          <div className="text-[11px] text-amber-700">
            ⚠ This value no longer exists in this workflow — the step or field it
            pointed to may have been renamed or removed.
          </div>
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Choose a new value
            </button>
            <button className="text-[11px] font-medium text-ink-500 hover:underline" onClick={remove} type="button">
              Remove
            </button>
          </div>
          <details className="mt-1">
            <summary className="cursor-pointer text-[10px] text-ink-400">View reference</summary>
            <div className="mt-1 break-all rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink-600">
              {binding.raw}
            </div>
          </details>
        </div>
      )}

      {binding.kind === 'literal' && (
        <div className="mt-1.5">
          <div className="rounded-md border border-ink-100 bg-brand-softer px-2 py-1.5 text-[11px] text-ink-700">
            {binding.value}
          </div>
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('enter')} type="button">
              Edit
            </button>
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Use previous step instead
            </button>
          </div>
        </div>
      )}

      {binding.kind === 'empty' && mode === 'idle' && (
        <div className="mt-1.5 flex gap-2">
          <button className="ui-button ui-button--secondary" onClick={() => setMode('enter')} type="button">
            Enter a value
          </button>
          <button className="ui-button ui-button--secondary" onClick={() => setMode('picker')} type="button">
            Use previous step
          </button>
        </div>
      )}

      {mode === 'enter' && !connected && (
        <div className="mt-1.5">
          <textarea
            autoFocus
            className="builder-field"
            onChange={event => setValue(event.target.value)}
            placeholder={target.description || 'Type a value…'}
            rows={2}
            value={binding.kind === 'literal' ? binding.value : ''}
          />
          <div className="mt-1 flex gap-2">
            <button className="text-[11px] font-medium text-ink-500 hover:underline" onClick={() => setMode('idle')} type="button">
              Done
            </button>
            <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={() => setMode('picker')} type="button">
              Use previous step instead
            </button>
          </div>
        </div>
      )}

      {mode === 'picker' && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <ValuePicker
            contract={contract}
            destinationHint={target.description}
            destinationKind="text"
            destinationLabel={target.label}
            onPick={field => { setValue(field.reference); setMode('idle'); }}
            previewValues={previewValues}
            selectedReference={binding.kind === 'resolved' ? binding.field.reference : undefined}
          />
          <button className="mt-1 text-[11px] font-medium text-ink-500 hover:underline" onClick={() => setMode('idle')} type="button">
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}

function ConnectedRow({
  currentReference,
  field,
  onRemove,
  onStartChange,
  preview,
  previewIsLive,
  stepLabel,
}: {
  currentReference: string;
  field: ContractField;
  onRemove: () => void;
  onStartChange: () => void;
  preview: string | null;
  previewIsLive: boolean;
  stepLabel: string;
}) {
  return (
    <div className="mt-1.5">
      <div className="text-[12px] text-ink-800">
        <span className="text-ink-400">←</span> <span className="font-medium">{leafLabel(field)}</span>
      </div>
      <div className="text-[11px] text-accent-700">From {stepLabel}</div>
      {preview && (
        <div className="mt-1 rounded-md border border-ink-100 bg-brand-softer px-2 py-1.5 text-[11px] text-ink-700">
          {previewIsLive && <span className="mr-1 font-semibold text-emerald-600">Ran:</span>}
          {previewIsLive ? `“${preview}”` : preview}
        </div>
      )}
      <div className="mt-1 flex gap-2">
        <button className="text-[11px] font-medium text-accent-700 hover:underline" onClick={onStartChange} type="button">
          Change
        </button>
        <button className="text-[11px] font-medium text-ink-500 hover:underline" onClick={onRemove} type="button">
          Remove
        </button>
      </div>
      <details className="mt-1">
        <summary className="cursor-pointer text-[10px] text-ink-400">View reference</summary>
        <div className="mt-1 break-all rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink-600">
          {currentReference}
        </div>
      </details>
    </div>
  );
}

function EmptyMappingState({ title, description }: { title: string; description: string }) {
  return (
    <div className="p-5">
      <div className="rounded-lg border border-dashed border-ink-200 bg-brand-softer p-5 text-center">
        <div className="text-sm font-semibold text-ink-800">{title}</div>
        <div className="mt-1 text-xs leading-5 text-ink-500">{description}</div>
      </div>
    </div>
  );
}
