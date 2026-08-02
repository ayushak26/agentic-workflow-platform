import type { Edge, Node } from 'reactflow';
import { outgoingEdges, sliceWorkflowThroughBranch, sliceWorkflowThroughNode } from './builder-graph';
import type { WorkflowEdgeData, WorkflowNodeData, YamlWorkflow } from './yaml-bridge';

export function BuilderTestPanel({
  workflow,
  selected,
  edges,
  onLaunchTest,
}: {
  workflow: YamlWorkflow;
  selected: Node<WorkflowNodeData> | null;
  edges: Edge<WorkflowEdgeData>[];
  nodes: Node<WorkflowNodeData>[];
  onLaunchTest: (workflow: YamlWorkflow, title: string) => void;
}) {
  if (!selected) {
    return (
      <div className="p-5">
        <div className="rounded-lg border border-dashed border-ink-200 bg-brand-softer p-5 text-center">
          <div className="text-sm font-semibold text-ink-800">Select a node to test</div>
          <div className="mt-1 text-xs leading-5 text-ink-500">
            A node test keeps its ancestors and stops at the selected node. It
            never changes the saved workflow.
          </div>
        </div>
      </div>
    );
  }

  const branches = outgoingEdges(selected.id, edges).filter(edge => edge.data?.edgeKind === 'branch');

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading">Test this node</div>
      <p className="mt-1 text-xs leading-5 text-ink-500">
        Runs a temporary slice of the workflow through the normal preflight,
        run API, and Cockpit. The saved workflow is never rewritten.
      </p>

      {branches.length === 0 ? (
        <button
          className="ui-button ui-button--primary mt-4 w-full justify-center"
          onClick={() => onLaunchTest(
            sliceWorkflowThroughNode(workflow, selected.id),
            `Node test: ${selected.data.nodeId}`,
          )}
          type="button"
        >
          Test through {selected.data.nodeId}
        </button>
      ) : (
        <div className="mt-4 space-y-2">
          <div className="text-xs font-semibold text-ink-800">
            This router has {branches.length} branch(es). Test one at a time:
          </div>
          {branches.map(branch => {
            const label = branch.data?.branchLabel ?? branch.target;
            return (
              <button
                className="ui-button ui-button--secondary w-full justify-between"
                key={branch.id}
                onClick={() => onLaunchTest(
                  sliceWorkflowThroughBranch(workflow, selected.id, branch.target, branch.data?.branchLabel),
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
