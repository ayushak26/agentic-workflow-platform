import { useMemo, useState } from 'react';
import type { Edge, Node } from 'reactflow';
import type { NodeTypeManifest } from '../../api/types';
import { buildVariableOptions } from './builder-graph';
import type {
  WorkflowEdgeData,
  WorkflowNodeData,
  YamlWorkflow,
} from './yaml-bridge';

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
  workflow,
  selected,
  nodes,
  edges,
  manifests,
  onConfigChange,
}: {
  workflow: Pick<YamlWorkflow, 'inputs' | 'static_variables'>;
  selected: Node<WorkflowNodeData> | null;
  nodes: Node<WorkflowNodeData>[];
  edges: Edge<WorkflowEdgeData>[];
  manifests: NodeTypeManifest[];
  onConfigChange: (next: Record<string, unknown>) => void;
}) {
  const [targetField, setTargetField] = useState('');
  const [sourceToken, setSourceToken] = useState('');
  const [query, setQuery] = useState('');
  const [copied, setCopied] = useState<string | null>(null);

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
  const variables = useMemo(() => (
    selected
      ? buildVariableOptions(workflow, selected.id, nodes, edges, manifests)
      : []
  ), [edges, manifests, nodes, selected, workflow]);
  const filteredVariables = variables.filter(option => (
    `${option.label} ${option.description} ${option.token}`
      .toLowerCase()
      .includes(query.trim().toLowerCase())
  ));
  const downstream = selected
    ? edges
      .filter(edge => edge.source === selected.id)
      .map(edge => nodes.find(node => node.id === edge.target)?.data.nodeId ?? edge.target)
    : [];

  if (!selected) {
    return (
      <EmptyMappingState
        title="Select a node to map its data"
        description="The mapper only shows workflow inputs and outputs that can reach the selected node."
      />
    );
  }

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading">Visual data mapping</div>
      <p className="mt-1 text-xs leading-5 text-ink-500">
        Connect an available value to a text-based configuration field. The
        Builder saves the mapping as the runtime&apos;s normal template syntax.
      </p>

      <section className="builder-mapping-card mt-4">
        <div className="builder-mapping-node">
          <span className="builder-mapping-port builder-mapping-port--in" />
          <div className="text-[10px] uppercase tracking-wide text-accent-700">Selected node</div>
          <div className="mt-1 truncate font-mono text-xs font-semibold text-ink-900">
            {selected.data.nodeId}
          </div>
          <div className="mt-1 text-[10px] text-ink-500">{selected.data.typeName}</div>
          <span className="builder-mapping-port builder-mapping-port--out" />
        </div>
        <div className="mt-3 text-[11px] text-ink-500">
          Contribution: produces data for {downstream.length > 0
            ? downstream.join(', ')
            : 'the workflow output'}.
        </div>
      </section>

      <section className="mt-4 rounded-lg border border-ink-100 bg-brand-softer p-3">
        <div className="text-xs font-semibold text-ink-800">Create mapping</div>
        <label className="mt-3 block text-[11px] font-medium text-ink-700">
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
        <label className="mt-3 block text-[11px] font-medium text-ink-700">
          Available value
          <select
            className="builder-field mt-1 font-mono"
            onChange={event => setSourceToken(event.target.value)}
            value={sourceToken}
          >
            <option value="">Choose an input or upstream output…</option>
            {(['Workflow inputs', 'Static variables', 'Upstream outputs'] as const).map(group => {
              const items = variables.filter(option => option.group === group);
              if (items.length === 0) return null;
              return (
                <optgroup key={group} label={group}>
                  {items.map(option => (
                    <option key={option.token} value={option.token}>{option.label}</option>
                  ))}
                </optgroup>
              );
            })}
          </select>
        </label>
        <button
          className="ui-button ui-button--primary mt-3 w-full justify-center"
          disabled={!targetField || !sourceToken}
          onClick={() => {
            if (!targetField || !sourceToken) return;
            onConfigChange({ ...selected.data.config, [targetField]: sourceToken });
          }}
          type="button"
        >
          Apply mapping
        </button>
        {targetField && typeof selected.data.config[targetField] === 'string' && (
          <div className="mt-2 rounded-md border border-ink-100 bg-white px-2 py-1.5 font-mono text-[10px] text-ink-600">
            Current: {String(selected.data.config[targetField])}
          </div>
        )}
      </section>

      <section className="mt-5">
        <div className="flex items-center justify-between gap-2">
          <div className="text-xs font-semibold text-ink-800">Variable picker</div>
          <div className="text-[10px] text-ink-500">{variables.length} available</div>
        </div>
        <input
          aria-label="Search available variables"
          className="builder-field mt-2"
          onChange={event => setQuery(event.target.value)}
          placeholder="Search inputs and outputs"
          type="search"
          value={query}
        />
        <div className="mt-2 space-y-1.5">
          {filteredVariables.map(option => (
            <button
              className="builder-variable-row"
              key={option.token}
              onClick={() => {
                navigator.clipboard.writeText(option.token).catch(() => undefined);
                setCopied(option.token);
                window.setTimeout(() => setCopied(null), 1200);
              }}
              type="button"
            >
              <span className="min-w-0 flex-1 text-left">
                <span className="block truncate text-[11px] font-semibold text-ink-800">{option.label}</span>
                <span className="block truncate font-mono text-[10px] text-accent-700">{option.token}</span>
              </span>
              <span className="text-[10px] font-semibold text-ink-500">
                {copied === option.token ? 'Copied' : 'Copy'}
              </span>
            </button>
          ))}
          {filteredVariables.length === 0 && (
            <div className="rounded-md border border-dashed border-ink-200 p-3 text-center text-xs text-ink-500">
              No available value matches this search.
            </div>
          )}
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
