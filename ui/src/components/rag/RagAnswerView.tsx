import { useState } from 'react';

/**
 * The RAG process, made understandable without opening internal traces.
 *
 * Shared by two surfaces that both run a saved Knowledge Studio RAG Agent and
 * need to show the same shape of result: the workflow RAG Agent node's
 * Output tab, and Knowledge Studio's own "Test query" panel. One component
 * keeps them from drifting into two different views of the same run.
 */

export type RagSourceView = {
  file_name: string;
  document_id?: string | null;
  locations: Array<{ page: number | null; section: string | null }>;
  metadata?: Record<string, unknown>;
};

export type RagRelevantContextItemView = {
  content: string;
  score: number | null;
  file_name: string;
  page_no: number | null;
  section: string | null;
};

export type ModelLabelLookup = (modelId: string) => string;

function sourceLink(source: RagSourceView): string | null {
  const link = source.metadata?.file_link ?? source.metadata?.source_uri;
  return typeof link === 'string' ? link : null;
}

function locationLabel(source: RagSourceView): string {
  const parts = source.locations
    .map(loc => (loc.page != null ? `p.${loc.page}` : loc.section ?? null))
    .filter((value): value is string => Boolean(value));
  return [...new Set(parts)].join(', ');
}

export function RagAnswerView({
  query,
  answer,
  sources,
  relevantContext,
  configuredModel,
  resolvedModel,
  modelLabel = model => model,
}: {
  query?: string;
  answer: string;
  sources: RagSourceView[];
  relevantContext: RagRelevantContextItemView[];
  configuredModel?: string;
  resolvedModel?: string;
  modelLabel?: ModelLabelLookup;
}) {
  const [contextOpen, setContextOpen] = useState(false);

  return (
    <div className="space-y-3 text-xs">
      {query && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">Query</div>
          <p className="mt-0.5 text-ink-800">{query}</p>
        </div>
      )}

      <div>
        <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">Answer</div>
        <p className="mt-0.5 whitespace-pre-wrap text-ink-900">{answer}</p>
      </div>

      {sources.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-400">
            Sources
          </div>
          <div className="mt-1 space-y-1">
            {sources.map((source, index) => {
              const link = sourceLink(source);
              const location = locationLabel(source);
              return (
                <div key={source.document_id ?? `${source.file_name}-${index}`} className="rounded border border-slate-100 p-2">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium text-ink-800">{source.file_name}</span>
                    {link && (
                      <a href={link} target="_blank" rel="noreferrer" className="flex-none text-accent-700 hover:underline">
                        Open source
                      </a>
                    )}
                  </div>
                  {location && <div className="mt-0.5 text-ink-500">{location}</div>}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {relevantContext.length > 0 && (
        <details open={contextOpen} onToggle={event => setContextOpen((event.target as HTMLDetailsElement).open)}>
          <summary className="cursor-pointer text-[10px] font-semibold uppercase tracking-wide text-ink-400">
            Retrieved context · {relevantContext.length} chunk{relevantContext.length === 1 ? '' : 's'}
          </summary>
          <div className="mt-1 space-y-1">
            {relevantContext.map((chunk, index) => (
              <div key={index} className="rounded border border-slate-100 bg-slate-50 p-2">
                <div className="flex items-center justify-between gap-2 text-ink-500">
                  <span className="truncate font-medium text-ink-700">{chunk.file_name}</span>
                  {chunk.score != null && <span className="flex-none">score {chunk.score.toFixed(2)}</span>}
                </div>
                <p className="mt-0.5 whitespace-pre-wrap text-ink-700">{chunk.content}</p>
              </div>
            ))}
          </div>
        </details>
      )}

      {(configuredModel || resolvedModel) && (
        <div className="text-ink-500">
          <span className="font-semibold uppercase tracking-wide text-[10px] text-ink-400">Model</span>{' '}
          {configuredModel && <span>{modelLabel(configuredModel)}</span>}
          {configuredModel && resolvedModel && resolvedModel !== configuredModel && (
            <span> → {modelLabel(resolvedModel)}</span>
          )}
        </div>
      )}
    </div>
  );
}
