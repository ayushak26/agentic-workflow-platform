import { useMemo, useState } from 'react';
import { api } from '../../../../api/client';
import { CopyButton } from '../../../../components/CopyButton';
import { RagAnswerView, type RagRelevantContextItemView, type RagSourceView } from '../../../../components/rag/RagAnswerView';
import { artifactLabel } from '../../file-artifact';
import type { NodeStatus } from '../../cockpit-state';
import { JsonTree } from '../JsonTree';
import { classifyArtifact, readableOutput } from '../node-render';

type RagAgentOutput = {
  query?: string;
  answer: string;
  sources?: RagSourceView[];
  relevant_context?: RagRelevantContextItemView[];
  answering_model?: string;
  resolved_answering_model?: string;
};

function download(text: string, filename: string) {
  const blob = new Blob([text], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function OutputTab({
  nodeId,
  typeName,
  status,
  output,
  streamingPreview,
}: {
  nodeId: string;
  typeName?: string;
  status: NodeStatus;
  output: unknown;
  streamingPreview?: string;
}) {
  const [query, setQuery] = useState('');
  const artifact = useMemo(() => classifyArtifact(output), [output]);
  const key = artifact?.key ?? null;
  const isImage = artifact?.isImage ?? false;
  const isFile = artifact?.isFile ?? false;

  if (output == null) {
    if (status === 'active' && streamingPreview) {
      return (
        <div className="p-3">
          <div className="mb-2 text-[11px] uppercase tracking-wide text-ink-500">
            Streaming — node is still running
          </div>
          <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-3 overflow-auto whitespace-pre-wrap max-h-full">
            {streamingPreview}
          </pre>
        </div>
      );
    }
    if (status === 'active') {
      return <div className="p-4 text-sm text-ink-500">Running — no output yet.</div>;
    }
    if (status === 'pending') {
      return <div className="p-4 text-sm text-ink-500">Waiting to start.</div>;
    }
    if (status === 'skipped') {
      return <div className="p-4 text-sm text-ink-500">Skipped — this branch wasn&rsquo;t taken.</div>;
    }
    if (status === 'cancelled') {
      return <div className="p-4 text-sm text-ink-500">Cancelled — the run ended before this node started.</div>;
    }
    return <div className="p-4 text-sm text-ink-500">No output recorded for this node.</div>;
  }

  const text = readableOutput(output);
  const isStructured = typeof output === 'object' && output !== null;

  return (
    <div className="p-3 h-full flex flex-col min-h-0">
      <div className="flex-none flex items-center gap-2 mb-2">
        {!isStructured && (
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search output…"
            className="flex-1 rounded-md border border-slate-300 px-2 py-1.5 text-xs"
          />
        )}
        <CopyButton text={typeof output === 'string' ? output : JSON.stringify(output, null, 2)} label="Copy" />
        <button
          type="button"
          onClick={() => download(text, `${nodeId}-output.txt`)}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
        >
          Download
        </button>
      </div>

      {isImage && key && (
        <div className="flex-none mb-3">
          <img src={api.fileUrl(key)} alt={nodeId} className="max-w-full rounded-md border border-slate-200" />
        </div>
      )}

      {isFile && key && (
        <div className="flex-none mb-3 flex items-center justify-between gap-3 rounded-md border border-accent-200 bg-accent-50 px-3 py-2">
          <div className="min-w-0">
            <div className="text-xs font-semibold text-accent-800">{artifactLabel(output, key)}</div>
            <div className="text-[11px] text-ink-500 truncate font-mono">{key.split('/').pop()}</div>
          </div>
          <button
            type="button"
            onClick={() => void api.downloadArtifact(key)}
            className="flex-none px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs font-medium hover:bg-accent-500"
          >
            Download
          </button>
        </div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto">
        {typeName === 'RAGAgent' && isStructured ? (
          <RagAnswerView
            query={(output as RagAgentOutput).query}
            answer={(output as RagAgentOutput).answer}
            sources={(output as RagAgentOutput).sources ?? []}
            relevantContext={(output as RagAgentOutput).relevant_context ?? []}
            configuredModel={(output as RagAgentOutput).answering_model}
            resolvedModel={(output as RagAgentOutput).resolved_answering_model}
          />
        ) : isStructured ? (
          <JsonTree value={output} searchable />
        ) : (
          <pre className="text-xs bg-slate-50 border border-slate-200 rounded-md p-3 whitespace-pre-wrap">
            {query
              ? text.split('\n').filter((line) => line.toLowerCase().includes(query.toLowerCase())).join('\n') || 'No matching lines.'
              : text}
          </pre>
        )}
      </div>
    </div>
  );
}
