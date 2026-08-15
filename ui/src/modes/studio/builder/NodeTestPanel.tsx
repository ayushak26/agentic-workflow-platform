import { useCallback, useMemo, useState } from 'react';
import type { Edge, Node } from 'reactflow';

import { api } from '../../../api/client';
import type { NodeTestResult, NodeTypeManifest } from '../../../api/types';
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

function parseJson(text: string): { value: Record<string, unknown>; error: string | null } {
  if (!text.trim()) return { value: {}, error: null };
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
      return { value: parsed as Record<string, unknown>, error: null };
    }
    return { value: {}, error: 'Expected a JSON object.' };
  } catch (error) {
    return { value: {}, error: error instanceof Error ? error.message : String(error) };
  }
}

export function NodeTestPanel({
  edges,
  manifest,
  onLaunchTest,
  selected,
  workflow,
}: {
  edges: Edge<WorkflowEdgeData>[];
  manifest: NodeTypeManifest | undefined;
  onLaunchTest: (workflow: YamlWorkflow, title: string) => void;
  selected: Node<WorkflowNodeData> | null;
  workflow: YamlWorkflow;
}) {
  const [inputsText, setInputsText] = useState('');
  const [upstreamText, setUpstreamText] = useState('');
  const [result, setResult] = useState<NodeTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const inputs = useMemo(() => parseJson(inputsText), [inputsText]);
  const upstream = useMemo(() => parseJson(upstreamText), [upstreamText]);

  const runTest = useCallback(() => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setResult(null);
    api.nodeTest({
      type_name: selected.data.typeName,
      node_id: selected.data.nodeId,
      config: selected.data.config,
      inputs: inputs.value,
      upstream_outputs: upstream.value,
    })
      .then(setResult)
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  }, [inputs.value, selected, upstream.value]);

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
          <label className="mt-3 block text-[11px] font-medium text-ink-700">
            Sample workflow inputs
            <textarea
              className="builder-field mt-1 font-mono"
              onChange={event => setInputsText(event.target.value)}
              placeholder={'{\n  "subject": "Pumpe ausgefallen",\n  "message": "Unsere Dura 15 Pumpe ist ausgefallen…"\n}'}
              rows={5}
              value={inputsText}
            />
          </label>
          {inputs.error && (
            <div className="mt-1 text-[10px] text-red-600">{inputs.error}</div>
          )}

          <label className="mt-3 block text-[11px] font-medium text-ink-700">
            Sample results from earlier steps
            <textarea
              className="builder-field mt-1 font-mono"
              onChange={event => setUpstreamText(event.target.value)}
              placeholder={'{\n  "understand_request": { "confidence": 0.64 }\n}'}
              rows={4}
              value={upstreamText}
            />
          </label>
          {upstream.error && (
            <div className="mt-1 text-[10px] text-red-600">{upstream.error}</div>
          )}
          <p className="mt-1 text-[10px] text-ink-500">
            Keyed by step id. Lets you test this step without running the ones
            before it — and lets you try a value the model rarely produces.
          </p>

          <button
            className="ui-button ui-button--primary mt-3 w-full justify-center"
            disabled={busy || Boolean(inputs.error) || Boolean(upstream.error)}
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
