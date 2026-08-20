import { useCallback, useMemo, useState } from 'react';
import type { Edge, Node } from 'reactflow';

import { api } from '../../../api/client';
import type {
  ContractField,
  LLMModelInfo,
  NodeTestResult,
  NodeTypeManifest,
  OutputContract,
} from '../../../api/types';
import {
  sliceWorkflowThroughBranch,
  sliceWorkflowThroughNode,
  outgoingEdges,
} from '../builder-graph';
import { InfoPopover } from './InfoPopover';
import type {
  WorkflowEdgeData,
  WorkflowNodeData,
  YamlWorkflow,
} from '../yaml-bridge';
import { ExplanationView, ValueTree } from './ExplanationView';
import { ModelSelect } from '../ModelSelect';
import { UpstreamSampleEditor, WorkflowInputsEditor } from './TestSampleEditor';

/**
 * The Test tab (§21, §22).
 *
 * Two kinds of test, because authors need both:
 *
 *   Test this step        run just this node against pasted sample data.
 *                         Seconds, no graph, tightest possible edit loop.
 *   Test through this step run the smallest valid upstream slice through the
 *                         normal run API, so a real chain is exercised.
 *
 * Neither modifies the saved workflow, and neither can trigger an external side
 * effect — the backend refuses a send from here on purpose, because a test is
 * something you run twenty times while adjusting wording.
 */

/** A placeholder value shaped like the field's type — not a guess at real
 *  content, just enough structure that the author is editing values, not
 *  inventing key names from scratch. */
function exampleValueForType(field: Pick<ContractField, 'type' | 'enum_values'>): unknown {
  switch (field.type) {
    case 'number':
    case 'integer':
      return 0;
    case 'boolean':
      return false;
    case 'enum':
      return field.enum_values[0] ?? '';
    case 'list':
      return [];
    case 'object':
      return {};
    default:
      return '';
  }
}

function setDeep(target: Record<string, unknown>, path: string, value: unknown): void {
  const segments = path.split('.');
  let cursor = target;
  for (const key of segments.slice(0, -1)) {
    const existing = cursor[key];
    if (typeof existing !== 'object' || existing === null || Array.isArray(existing)) {
      cursor[key] = {};
    }
    cursor = cursor[key] as Record<string, unknown>;
  }
  cursor[segments[segments.length - 1]] = value;
}

/** Skeleton "Sample workflow inputs" built from the contract's declared
 *  workflow inputs — file inputs are skipped, since a file can't be pasted
 *  as JSON test data. */
function buildSampleInputs(contract: OutputContract | null): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const input of contract?.inputs ?? []) {
    if (input.type === 'file') continue;
    out[input.name] = input.type === 'json' ? {} : '';
  }
  return out;
}

/** Skeleton "Sample results from earlier steps" built from the typed fields
 *  each upstream node is known to produce — one nested object per node id,
 *  built from its dotted field paths (e.g. "parsed.intent"). */
function buildSampleUpstream(contract: OutputContract | null): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const node of contract?.nodes ?? []) {
    if (node.fields.length === 0) continue;
    const nodeSample: Record<string, unknown> = {};
    for (const field of node.fields) {
      setDeep(nodeSample, field.path, exampleValueForType(field));
    }
    out[node.node_id] = nodeSample;
  }
  return out;
}

export function NodeTestPanel({
  contract,
  edges,
  llmModels,
  manifest,
  onLaunchTest,
  onNodeRunOutput,
  selected,
  workflow,
}: {
  contract: OutputContract | null;
  edges: Edge<WorkflowEdgeData>[];
  llmModels: LLMModelInfo[];
  manifest: NodeTypeManifest | undefined;
  onLaunchTest: (workflow: YamlWorkflow, title: string) => void;
  onNodeRunOutput?: (nodeId: string, output: Record<string, unknown> | null | undefined) => void;
  selected: Node<WorkflowNodeData> | null;
  workflow: YamlWorkflow;
}) {
  // Chosen/written values from the structured editors below — no JSON
  // anywhere in this panel; every value comes from a labeled input box.
  const [structuredInputs, setStructuredInputs] = useState<Record<string, unknown>>({});
  const [structuredUpstream, setStructuredUpstream] = useState<Record<string, unknown>>({});
  const [testModel, setTestModel] = useState('auto');
  const [result, setResult] = useState<NodeTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [prefilledForNode, setPrefilledForNode] = useState<string | null>(null);

  const supportsModel = Boolean(
    manifest?.uses_ai
    || (manifest?.config_schema as { properties?: Record<string, unknown> } | undefined)
      ?.properties?.model,
  );

  // Prefill the two sections from the contract once per node selection —
  // not on every contract refetch, so editing something elsewhere in the
  // workflow (which reruns the debounced output-contract fetch) never clobbers
  // a value the author already typed in here. Adjusting state directly during
  // render (rather than in an effect) on a prop change is the React-endorsed
  // pattern for this — it applies before the browser paints, so there's no
  // flash of the old value.
  if (selected && contract && prefilledForNode !== selected.data.nodeId) {
    setPrefilledForNode(selected.data.nodeId);
    setStructuredInputs(buildSampleInputs(contract));
    setStructuredUpstream(buildSampleUpstream(contract));
    setTestModel(
      typeof selected.data.config.model === 'string' ? selected.data.config.model : 'auto',
    );
  }

  const runTest = useCallback(() => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const nodeId = selected.data.nodeId;
    api.nodeTest({
      type_name: selected.data.typeName,
      node_id: nodeId,
      config: supportsModel ? { ...selected.data.config, model: testModel } : selected.data.config,
      inputs: structuredInputs,
      upstream_outputs: structuredUpstream,
      workflow_name: workflow.name,
    })
      .then(next => {
        setResult(next);
        if (next.status === 'completed') onNodeRunOutput?.(nodeId, next.output);
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  }, [onNodeRunOutput, selected, structuredInputs, structuredUpstream, supportsModel, testModel, workflow.name]);

  if (!selected) {
    return (
      <div className="p-5">
        <div className="rounded-lg border border-dashed border-ink-200 bg-brand-softer p-5 text-center">
          <div className="text-sm font-semibold text-ink-800">Select a step to test</div>
          <div className="mt-1 text-xs leading-5 text-ink-500">
            You can run one step against a pasted example, or run the workflow up
            to it. Neither changes what is saved.
          </div>
        </div>
      </div>
    );
  }

  const branches = outgoingEdges(selected.id, edges)
    .filter(edge => edge.data?.edgeKind === 'branch');
  const externalWrite = manifest?.external_action
    && ['send', 'reply'].includes(String(selected.data.config.operation ?? ''));

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading flex items-center gap-1.5">
        Test this step
        <InfoPopover feature="node_testing" />
      </div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Runs only <span className="font-mono">{selected.data.nodeId}</span>,
        against the example below. Real execution — an AI step really calls the
        model — but no run record and no change to the saved workflow.
      </p>

      {externalWrite ? (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3 text-[11px] leading-4 text-amber-900">
          This step sends something outside the platform, so it cannot be run
          from here. Switch the operation to Search, Read or Create Draft to test
          the configuration, and use a real run when you intend the action.
        </div>
      ) : (
        <>
          {supportsModel && (
            <label className="mt-3 block text-[11px] font-medium text-ink-700">
              Model for this test
              <ModelSelect
                className="mt-1"
                llmModels={llmModels}
                onChange={setTestModel}
                value={testModel}
              />
              <span className="mt-1 block text-[10px] font-normal text-ink-500">
                Overrides the configured model for this test run only — the saved
                step keeps whatever model it's set to.
              </span>
            </label>
          )}

          <label className="mt-3 block text-[11px] font-medium text-ink-700">
            Sample workflow inputs
          </label>
          <WorkflowInputsEditor
            contract={contract}
            onChange={setStructuredInputs}
            values={structuredInputs}
          />

          <label className="mt-4 block text-[11px] font-medium text-ink-700">
            Sample results from earlier steps
          </label>
          <p className="mt-1 text-[10px] text-ink-500">
            Lets you test this step without running the ones before it — and
            lets you try a value the model rarely produces.
          </p>
          <UpstreamSampleEditor
            contract={contract}
            onChange={setStructuredUpstream}
            values={structuredUpstream}
          />

          <button
            className="ui-button ui-button--primary mt-3 w-full justify-center"
            disabled={busy}
            onClick={runTest}
            type="button"
          >
            {busy ? 'Running…' : 'Run test'}
          </button>
        </>
      )}

      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-[11px] leading-4 text-red-800">
          {error}
        </div>
      )}

      {result && (
        <section className="mt-4">
          <div className="flex items-center justify-between">
            <div className="text-[11px] font-semibold text-ink-800">
              {result.status === 'completed' ? 'Output' : 'This step failed'}
            </div>
            <span className="text-[10px] text-ink-500">{result.duration_s}s</span>
          </div>

          {result.status === 'failed' ? (
            <div className="mt-2 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] leading-4 text-red-800">
              <div className="font-mono text-[10px]">{result.error_type}</div>
              <div className="mt-1">{result.error}</div>
            </div>
          ) : (
            <>
              {result.explanation && (
                <div className="mt-2">
                  <ExplanationView explanation={result.explanation} />
                </div>
              )}
              <div className="mt-2 rounded-md border border-slate-200 bg-white p-2">
                <ValueTree value={result.output} />
              </div>
            </>
          )}

          {result.resolved_config && (
            <details className="mt-2">
              <summary className="cursor-pointer text-[10px] text-ink-500">
                What this step actually received after mapping
              </summary>
              <pre className="mt-1 max-h-48 overflow-auto rounded bg-slate-50 p-2 font-mono text-[10px] text-ink-700">
                {JSON.stringify(result.resolved_config, null, 2)}
              </pre>
            </details>
          )}
        </section>
      )}

      <hr className="my-5 border-slate-200" />

      <div className="builder-panel-heading flex items-center gap-1.5">
        Test the workflow up to here
        <InfoPopover feature="branch_testing" />
      </div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Runs the smallest valid slice of the workflow ending at this step,
        through the normal preflight, run API and Cockpit. The saved workflow is
        never rewritten.
      </p>

      {branches.length === 0 ? (
        <button
          className="ui-button ui-button--secondary mt-3 w-full justify-center"
          onClick={() => onLaunchTest(
            sliceWorkflowThroughNode(workflow, selected.id),
            `Node test: ${selected.data.nodeId}`,
          )}
          type="button"
        >
          Run through {selected.data.nodeId}
        </button>
      ) : (
        <div className="mt-3 space-y-2">
          <div className="text-[11px] font-medium text-ink-800">
            This router has {branches.length} branch{branches.length === 1 ? '' : 'es'}.
            Test one at a time:
          </div>
          {branches.map(branch => {
            const label = branch.data?.branchLabel ?? branch.target;
            return (
              <button
                className="ui-button ui-button--secondary w-full justify-between"
                key={branch.id}
                onClick={() => onLaunchTest(
                  sliceWorkflowThroughBranch(
                    workflow,
                    selected.id,
                    branch.target,
                    branch.data?.branchLabel,
                  ),
                  `Branch test: ${label}`,
                )}
                type="button"
              >
                <span>{label}</span>
                <span className="font-mono text-[11px] text-ink-500">→ {branch.target}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
