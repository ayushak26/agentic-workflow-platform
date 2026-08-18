import { useEffect, useState } from 'react';
import type { NavigateFunction } from 'react-router-dom';
import type { CostLedgerEntry, NodeRun, NodeTypeManifest, RunDetail } from '../../../api/types';
import { STATUS_LABEL, type NodeStatus } from '../cockpit-state';
import { WorkflowVariablesPanel } from '../WorkflowVariablesPanel';
import { clock, outputSummary, typeStyle } from './node-render';
import { JsonTree } from './JsonTree';
import { OutputTab } from './tabs/OutputTab';
import { ErrorsTab } from './tabs/ErrorsTab';
import { LogsTab } from './tabs/LogsTab';
import { NodeTypeAskAi } from '../NodeTypeAskAi';

export type SelectedNodeInfo = {
  id: string;
  typeName: string;
  status: NodeStatus;
};

type TabKey = 'overview' | 'input' | 'output' | 'logs' | 'errors' | 'metadata' | 'performance';

const TABS: { key: TabKey; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'input', label: 'Input' },
  { key: 'output', label: 'Output' },
  { key: 'logs', label: 'Logs' },
  { key: 'errors', label: 'Errors' },
  { key: 'metadata', label: 'Metadata' },
  { key: 'performance', label: 'Performance' },
];

function durationLabel(seconds: number | null | undefined): string {
  if (seconds == null) return '—';
  return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds.toFixed(2)}s`;
}

function OverviewTabContent({
  node,
  nodeRun,
  typeInfo,
  runStatus,
}: {
  node: SelectedNodeInfo;
  nodeRun: NodeRun | undefined;
  typeInfo: NodeTypeManifest | undefined;
  runStatus: string | undefined;
}) {
  const ts = typeStyle(node.typeName);
  const [askingAi, setAskingAi] = useState(false);
  // Only meaningful once the run has stopped changing under it — while a
  // run is still "running", asking about a node type mid-execution isn't
  // useful and adds load during the part of the lifecycle that matters most.
  const askAiAvailable = runStatus !== 'running';
  return (
    <div className="p-3 space-y-3 text-xs">
      <div className="flex items-center gap-2">
        <span className={`h-2.5 w-2.5 rounded-full flex-none ${ts.dot}`} />
        <span className="font-mono text-sm text-ink-900">{node.id}</span>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Type</div>
          <div className="text-ink-900">{ts.label}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Category</div>
          <div className="text-ink-900">{typeInfo?.category ?? '—'}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Status</div>
          <div className="text-ink-900">{STATUS_LABEL[node.status]}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Started</div>
          <div className="text-ink-900">{clock(nodeRun?.started_at ?? null)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Ended</div>
          <div className="text-ink-900">{clock(nodeRun?.ended_at ?? null)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Duration</div>
          <div className="text-ink-900">{durationLabel(nodeRun?.duration_s)}</div>
        </div>
        {nodeRun?.output != null && (
          <div>
            <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Output</div>
            <div className="text-ink-900">{outputSummary(nodeRun.output) ?? '—'}</div>
          </div>
        )}
      </div>
      {typeInfo?.description && (
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">What this node does</div>
          <div className="text-ink-700">{typeInfo.description}</div>
        </div>
      )}
      <button
        type="button"
        onClick={() => setAskingAi(true)}
        disabled={!askAiAvailable}
        title={askAiAvailable ? undefined : 'Available once the run finishes, fails, or pauses'}
        className="text-accent-700 hover:underline disabled:text-ink-400 disabled:no-underline disabled:cursor-not-allowed"
      >
        Ask AI about this node type →
      </button>
      {askingAi && <NodeTypeAskAi typeName={node.typeName} onClose={() => setAskingAi(false)} />}
    </div>
  );
}

function InputTabContent({ nodeRun }: { nodeRun: NodeRun | undefined }) {
  if (!nodeRun || nodeRun.input == null || Object.keys(nodeRun.input).length === 0) {
    return <div className="p-4 text-sm text-ink-500">No input recorded for this node.</div>;
  }
  return (
    <div className="p-3">
      <JsonTree value={nodeRun.input} />
    </div>
  );
}

function MetadataTabContent({ nodeRun }: { nodeRun: NodeRun | undefined }) {
  const selections = nodeRun?.model_selections ?? [];
  if (selections.length === 0) {
    return <div className="p-4 text-sm text-ink-500">No model-selection metadata for this node.</div>;
  }
  return (
    <div className="p-3 space-y-2">
      {selections.map((selection, index) => (
        <div
          key={`${selection.call_id}:${index}`}
          className="rounded border border-accent-200 bg-white p-2 text-xs"
        >
          <div className="font-semibold text-accent-800">
            {selection.actual_model}
            {selection.fallback ? ' · fallback' : ''}
            {selection.cache_hit ? ' · cache hit' : ''}
          </div>
          <div className="mt-1 text-ink-500">
            Requested {selection.requested_model} · {selection.mode} · {selection.complexity}{' '}
            {selection.task_kind.replace('_', ' ')}
          </div>
          <div className="mt-1 text-ink-700">{selection.reason}</div>
        </div>
      ))}
    </div>
  );
}

function costLabel(usd: number): string {
  return usd > 0 && usd < 0.0001 ? '<$0.0001' : `$${usd.toFixed(4)}`;
}

function AICostCallCard({ entry }: { entry: CostLedgerEntry }) {
  return (
    <div className="rounded border border-slate-200 bg-white p-2">
      <div className="flex items-center justify-between">
        <span className="font-mono text-ink-900">{entry.model || '—'}</span>
        <span className="font-semibold text-ink-900">
          {entry.no_model_charge ? 'No model charge' : costLabel(entry.cost_usd)}
        </span>
      </div>
      <div className="mt-1 text-ink-500">
        {entry.task_type.replace(/_/g, ' ')}
        {entry.stage ? ` · ${entry.stage.replace(/_/g, ' ')}` : ''}
        {entry.provider !== 'unknown' ? ` · ${entry.provider}` : ''}
      </div>
      {!entry.no_model_charge && (
        <div className="mt-1 text-ink-500">
          {entry.input_tokens.toLocaleString()} in / {entry.output_tokens.toLocaleString()} out tokens
          {entry.latency_ms != null ? ` · ${(entry.latency_ms / 1000).toFixed(2)}s` : ''}
        </div>
      )}
      {entry.fallback_used && (
        <div className="mt-1 text-warn">
          Fallback — requested {entry.intended_model}
          {entry.fallback_reason ? `: ${entry.fallback_reason}` : ''}
        </div>
      )}
      {entry.cost_source === 'provider_reported' && !entry.no_model_charge && (
        <div className="mt-1 text-ink-400">Provider-reported cost</div>
      )}
    </div>
  );
}

function PerformanceTabContent({
  nodeRun,
  costEntries,
}: {
  nodeRun: NodeRun | undefined;
  costEntries: CostLedgerEntry[];
}) {
  const totalCost = costEntries.reduce((sum, e) => sum + e.cost_usd, 0);
  return (
    <div className="p-3 space-y-3 text-xs">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Duration</div>
          <div className="text-ink-900">{durationLabel(nodeRun?.duration_s)}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">Model calls</div>
          <div className="text-ink-900">{nodeRun?.model_selections?.length ?? costEntries.length}</div>
        </div>
        <div>
          <div className="text-[11px] uppercase tracking-wide text-ink-500 mb-1">AI cost</div>
          <div className="text-ink-900">{costLabel(totalCost)}</div>
        </div>
      </div>
      {costEntries.length === 0 ? (
        <div className="text-ink-400">
          No AI cost recorded for this activity yet — it may still be running, or made no
          model calls.
        </div>
      ) : (
        <div className="space-y-2">
          <div className="text-[11px] uppercase tracking-wide text-ink-500">AI execution</div>
          {costEntries.map((entry, index) => (
            <AICostCallCard key={`${entry.node_id}:${index}`} entry={entry} />
          ))}
        </div>
      )}
    </div>
  );
}

export function NodeInspector({
  selectedNode,
  nodeRun,
  streamingPreview,
  run,
  navigate,
  workflowVariables,
  fullscreen,
  onToggleFullscreen,
  live = true,
  nodeTypesByName = {},
  costEntries = [],
}: {
  selectedNode: SelectedNodeInfo | null;
  nodeRun: NodeRun | undefined;
  streamingPreview?: string;
  run: RunDetail | null;
  navigate: NavigateFunction;
  workflowVariables: { inputs: Record<string, unknown>; variables: Record<string, unknown>; outputs: Record<string, unknown> };
  fullscreen: boolean;
  onToggleFullscreen: () => void;
  // Cockpit shows a still-executing run (default); Run History shows a
  // historical one — WorkflowVariablesPanel's copy differs accordingly
  // ("Live workflow variables" vs. a plain review of what this run used).
  live?: boolean;
  // Node-type manifest keyed by type_name — supplies the category +
  // description shown on the Overview tab. Optional so callers that haven't
  // fetched it yet just render without that section.
  nodeTypesByName?: Record<string, NodeTypeManifest>;
  // Every AI-cost ledger entry for the run (not just this node) — filtered
  // to the selected node below. Undefined until the run completes and
  // /api/cost/run/{id} resolves.
  costEntries?: CostLedgerEntry[];
}) {
  const [tab, setTab] = useState<TabKey>('overview');
  const hasError = Boolean(nodeRun?.error);

  // Jumping to a different node always lands back on Overview rather than
  // keeping whatever tab was open for the previous node — Errors staying
  // open on a healthy node would be a confusing carry-over.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setTab('overview');
  }, [selectedNode?.id]);

  if (!selectedNode) {
    return (
      <div className="h-full overflow-y-auto bg-white">
        <div className="px-4 py-3 border-b border-slate-200 text-xs text-ink-500">
          No node selected — showing workflow-level variables.
        </div>
        <WorkflowVariablesPanel
          live={live}
          inputs={workflowVariables.inputs}
          variables={workflowVariables.variables}
          outputs={workflowVariables.outputs}
        />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col min-h-0 bg-white">
      <div className="flex-none flex items-center justify-between px-3 pt-2.5">
        <div className="min-w-0">
          <div className="font-mono text-sm text-ink-900 truncate">{selectedNode.id}</div>
        </div>
        <button
          type="button"
          onClick={onToggleFullscreen}
          title={fullscreen ? 'Exit full-screen output' : 'Maximize this panel'}
          className="flex-none text-ink-400 hover:text-ink-700 text-xs"
        >
          {fullscreen ? '⤡ Exit full screen' : '⤢ Full screen'}
        </button>
      </div>
      <div className="flex-none flex border-b border-slate-200 mt-2 overflow-x-auto">
        {TABS.map(({ key, label }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`flex-none px-3 py-2 text-xs whitespace-nowrap ${
              tab === key
                ? 'border-b-2 border-accent-600 font-medium text-ink-900'
                : 'text-ink-500'
            } ${key === 'errors' && hasError ? 'text-bad' : ''}`}
          >
            {label}
            {key === 'errors' && hasError && <span className="ml-1">&#9888;</span>}
          </button>
        ))}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto">
        {tab === 'overview' && (
          <OverviewTabContent
            node={selectedNode}
            nodeRun={nodeRun}
            typeInfo={nodeTypesByName[selectedNode.typeName]}
            runStatus={run?.status}
          />
        )}
        {tab === 'input' && <InputTabContent nodeRun={nodeRun} />}
        {tab === 'output' && (
          <OutputTab
            nodeId={selectedNode.id}
            typeName={selectedNode.typeName}
            status={selectedNode.status}
            output={nodeRun?.output ?? null}
            streamingPreview={streamingPreview}
          />
        )}
        {tab === 'logs' && <LogsTab nodeRun={nodeRun} />}
        {tab === 'errors' && <ErrorsTab nodeRun={nodeRun} run={run} navigate={navigate} />}
        {tab === 'metadata' && <MetadataTabContent nodeRun={nodeRun} />}
        {tab === 'performance' && (
          <PerformanceTabContent
            nodeRun={nodeRun}
            costEntries={costEntries.filter((entry) => entry.node_id === selectedNode.id)}
          />
        )}
      </div>
    </div>
  );
}
