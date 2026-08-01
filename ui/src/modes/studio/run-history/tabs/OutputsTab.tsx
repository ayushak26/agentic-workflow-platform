import { useMemo, useState } from 'react';
import { api } from '../../../../api/client';
import { CopyButton } from '../../../../components/CopyButton';
import type { RunDetail } from '../../../../api/types';
import { artifactLabel } from '../../file-artifact';
import { classifyArtifact, outputSummary, readableOutput, typeStyle } from '../../cockpit/node-render';

type OutputCard = { nodeId: string; typeName: string | undefined; output: unknown };

function buildCards(run: RunDetail): OutputCard[] {
  const nodeRunById = run.node_runs ?? {};
  const ids = new Set([...Object.keys(nodeRunById), ...Object.keys(run.outputs ?? {})]);
  return Array.from(ids)
    .map((nodeId) => ({
      nodeId,
      typeName: nodeRunById[nodeId]?.type_name ?? run.node_types?.[nodeId],
      output: nodeRunById[nodeId]?.output ?? run.outputs?.[nodeId],
    }))
    .filter((card) => card.output != null);
}

function OutputCardView({
  card, onOpen,
}: {
  card: OutputCard;
  onOpen: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const ts = typeStyle(card.typeName);
  const artifact = classifyArtifact(card.output);
  const preview = outputSummary(card.output) ?? readableOutput(card.output).slice(0, 120);

  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden bg-white">
      <div className="flex items-center justify-between gap-2 px-3 py-2.5">
        <div className="min-w-0 flex items-center gap-2">
          <span className={`h-2.5 w-2.5 rounded-full flex-none ${ts.dot}`} />
          <span className="font-mono text-sm text-ink-900 truncate">{card.nodeId}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded flex-none ${ts.chip}`}>{ts.label}</span>
        </div>
        <div className="flex-none flex items-center gap-1.5">
          <CopyButton text={readableOutput(card.output)} label="Copy" />
          {artifact && (
            <button
              type="button"
              onClick={() => void api.downloadArtifact(artifact.key)}
              className="rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
            >
              Download
            </button>
          )}
          <button
            type="button"
            onClick={onOpen}
            className="rounded border border-accent-300 bg-accent-50 px-2 py-1 text-[10px] text-accent-700 hover:bg-accent-100"
          >
            Open
          </button>
        </div>
      </div>

      {artifact?.isImage && (
        <div className="px-3 pb-2.5">
          <img
            src={api.fileUrl(artifact.key)}
            alt={card.nodeId}
            className="max-h-40 rounded-md border border-slate-200"
          />
        </div>
      )}
      {artifact?.isFile && (
        <div className="mx-3 mb-2.5 flex items-center justify-between gap-2 rounded-md bg-accent-50 px-2.5 py-1.5 text-xs text-accent-800">
          {artifactLabel(card.output, artifact.key)}
        </div>
      )}

      <div className="px-3 pb-2.5">
        {expanded ? (
          <pre className="text-[11px] bg-slate-50 border border-slate-200 rounded-md p-2 max-h-64 overflow-auto whitespace-pre-wrap">
            {readableOutput(card.output)}
          </pre>
        ) : (
          <button
            type="button"
            onClick={() => setExpanded(true)}
            className="text-xs text-ink-600 hover:text-ink-900 text-left truncate w-full"
          >
            {preview || 'No preview available'}
          </button>
        )}
      </div>
    </div>
  );
}

export function OutputsTab({
  run,
  onOpenNode,
}: {
  run: RunDetail;
  onOpenNode: (nodeId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [groupByStage, setGroupByStage] = useState(false);
  const cards = useMemo(() => buildCards(run), [run]);
  const filtered = cards.filter((c) => (
    !query || c.nodeId.toLowerCase().includes(query.toLowerCase())
    || (c.typeName ?? '').toLowerCase().includes(query.toLowerCase())
  ));

  if (cards.length === 0) {
    return <div className="p-6 text-sm text-ink-500">This run hasn&rsquo;t produced any node outputs yet.</div>;
  }

  return (
    <div className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search outputs by node name or type…"
          className="flex-1 min-w-[180px] rounded-md border border-slate-300 px-2.5 py-1.5 text-xs"
        />
        <label className="flex items-center gap-1.5 text-xs text-ink-700 flex-none">
          <input type="checkbox" checked={groupByStage} onChange={(e) => setGroupByStage(e.target.checked)} />
          Group by type
        </label>
      </div>
      <div className={groupByStage ? 'space-y-4' : 'grid grid-cols-1 sm:grid-cols-2 gap-3'}>
        {groupByStage ? (
          Object.entries(
            filtered.reduce<Record<string, OutputCard[]>>((acc, card) => {
              const key = card.typeName ?? 'Other';
              (acc[key] ??= []).push(card);
              return acc;
            }, {}),
          ).map(([type, group]) => (
            <div key={type}>
              <div className="text-xs font-medium text-ink-500 mb-2">{type}</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {group.map((card) => (
                  <OutputCardView key={card.nodeId} card={card} onOpen={() => onOpenNode(card.nodeId)} />
                ))}
              </div>
            </div>
          ))
        ) : (
          filtered.map((card) => (
            <OutputCardView key={card.nodeId} card={card} onOpen={() => onOpenNode(card.nodeId)} />
          ))
        )}
      </div>
    </div>
  );
}
