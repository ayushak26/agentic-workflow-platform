import { useMemo, useState } from 'react';
import type { Edge, Node } from 'reactflow';
import type { ContractField, NodeTypeManifest, OutputContract } from '../../api/types';
import { FieldDetail, FieldPicker } from './builder/FieldPicker';
import type {
  WorkflowEdgeData,
  WorkflowNodeData,
  YamlWorkflow,
} from './yaml-bridge';

/**
 * The Inputs tab — where a step's configuration is connected to upstream values.
 *
 * Clicking a field creates the mapping (§14). The author picks the target
 * configuration field, then picks the value from a typed tree that only ever
 * contains things this step can actually read, and the Builder writes the
 * template reference for them. Typing `{{outputs.extract.customer.company}}` by
 * hand remains possible — the field stays editable — but is no longer the way
 * the product works.
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

export function DataMappingPanel({
  contract,
  edges,
  manifests,
  nodes,
  onConfigChange,
  selected,
}: {
  contract: OutputContract | null;
  edges: Edge<WorkflowEdgeData>[];
  manifests: NodeTypeManifest[];
  nodes: Node<WorkflowNodeData>[];
  onConfigChange: (next: Record<string, unknown>) => void;
  selected: Node<WorkflowNodeData> | null;
  workflow: Pick<YamlWorkflow, 'inputs' | 'static_variables'>;
}) {
  const [targetField, setTargetField] = useState('');
  const [picked, setPicked] = useState<ContractField | null>(null);

  const manifest = selected
    ? manifests.find(item => item.type_name === selected.data.typeName)
    : undefined;
  const properties = (
    manifest?.config_schema as { properties?: Record<string, PropertySchema> }
  )?.properties ?? {};
  const targetFields = Object.entries(properties)
    .filter(([, schema]) => acceptsText(schema))
    .map(([name, schema]) => ({
      name,
      label: schema.title || name.replace(/_/g, ' '),
      description: schema.description,
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
        title="Select a step to connect its data"
        description="The picker only shows values that can actually reach the selected step."
      />
    );
  }

  const currentValue = targetField ? selected.data.config[targetField] : undefined;

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading">What this step reads</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Pick a configuration field, then click the value to connect. Only values
        from steps that always run before this one are offered.
      </p>

      <section className="builder-mapping-card mt-4">
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

      <section className="mt-4 rounded-lg border border-ink-100 bg-brand-softer p-3">
        <label className="block text-[11px] font-medium text-ink-700">
          Configuration field
          <select
            className="builder-field mt-1"
            onChange={event => setTargetField(event.target.value)}
            value={targetField}
          >
            <option value="">Choose a field…</option>
            {targetFields.map(field => (
              <option key={field.name} value={field.name}>{field.label}</option>
            ))}
          </select>
        </label>

        {targetField && typeof currentValue === 'string' && currentValue && (
          <div className="mt-2 rounded-md border border-ink-100 bg-white px-2 py-1.5 font-mono text-[10px] text-ink-600">
            Currently: {currentValue}
          </div>
        )}

        {picked && (
          <div className="mt-2">
            <FieldDetail field={picked} />
          </div>
        )}

        <button
          className="ui-button ui-button--primary mt-3 w-full justify-center"
          disabled={!targetField || !picked}
          onClick={() => {
            if (!targetField || !picked) return;
            onConfigChange({ ...selected.data.config, [targetField]: picked.reference });
          }}
          type="button"
        >
          Connect this value
        </button>
      </section>

      <section className="mt-4">
        <div className="text-[11px] font-semibold text-ink-800">Available values</div>
        <div className="mt-2">
          <FieldPicker
            contract={contract}
            onPick={field => {
              setPicked(field);
              // Picking a value with a target already chosen is the whole
              // interaction — apply it immediately rather than making the author
              // confirm what they just clicked.
              if (targetField) {
                onConfigChange({ ...selected.data.config, [targetField]: field.reference });
              }
            }}
            selectedReference={picked?.reference}
          />
        </div>
      </section>
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
