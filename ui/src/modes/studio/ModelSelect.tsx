import { useEffect, useRef, useState } from 'react';

import { api } from '../../api/client';
import type { LLMModelInfo, OpenRouterModelInfo } from '../../api/types';
import { Icon } from '../../components/ui/Icon';

/**
 * Searchable model picker: the small, curated catalog (llmModels — includes the single
 * "Auto" option) plus a live search over OpenRouter's ~500-model catalog
 * (GET /api/llm/models/openrouter, TTL-cached server-side — see app/llm/openrouter_catalog.py).
 * Picking an OpenRouter result sets the same plain model-id string as picking a catalog
 * entry (e.g. "openrouter/openai/gpt-4o-mini") — no separate "OpenRouter mode" toggle.
 */
export function ModelSelect({
  value,
  llmModels,
  onChange,
  className,
}: {
  value: string;
  llmModels: LLMModelInfo[];
  onChange: (next: string) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [openRouterResults, setOpenRouterResults] = useState<OpenRouterModelInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const timer = window.setTimeout(() => {
      setLoading(true);
      api.llmOpenRouterModels(query.trim())
        .then(result => setOpenRouterResults(result.models))
        .catch(() => setOpenRouterResults([]))
        .finally(() => setLoading(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [query, open]);

  useEffect(() => {
    if (!open) return;
    const handleClick = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [open]);

  const needle = query.trim().toLowerCase();
  const filteredCatalog = needle
    ? llmModels.filter(model =>
      model.display_name.toLowerCase().includes(needle)
      || model.name.toLowerCase().includes(needle))
    : llmModels;

  const selectedCatalogModel = llmModels.find(model => model.name === value);
  const selectedLabel = selectedCatalogModel?.display_name ?? value;

  const choose = (next: string) => {
    onChange(next);
    setQuery('');
    setOpen(false);
  };

  return (
    <div ref={containerRef} className={`relative ${className ?? ''}`}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between rounded-md border border-slate-300 bg-white px-2 py-2 text-left text-sm"
      >
        <span className="truncate">{selectedLabel || 'Choose a model…'}</span>
        <Icon name={open ? 'chevron-left' : 'chevron-right'} size={12} />
      </button>
      {open && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center gap-2 border-b border-slate-100 p-2">
            <Icon name="search" size={13} />
            <input
              autoFocus
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Search the catalog or OpenRouter's ~500 models…"
              className="w-full border-none text-sm focus:outline-none"
            />
          </div>
          <div className="max-h-80 overflow-y-auto">
            {filteredCatalog.length > 0 && (
              <div>
                <div className="px-2 pt-2 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                  Catalog
                </div>
                {filteredCatalog.map(model => (
                  <button
                    key={model.name}
                    type="button"
                    onClick={() => choose(model.name)}
                    className="flex w-full items-center justify-between px-2 py-1.5 text-left text-sm hover:bg-accent-50"
                  >
                    <span>
                      {model.display_name}
                      {!model.configured && !model.automatic ? ' — not configured' : ''}
                    </span>
                    {model.name === value && <Icon name="check" size={12} />}
                  </button>
                ))}
              </div>
            )}
            <div>
              <div className="px-2 pt-2 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                OpenRouter {loading ? '(searching…)' : `(${openRouterResults.length})`}
              </div>
              {openRouterResults.map(model => (
                <button
                  key={model.id}
                  type="button"
                  onClick={() => choose(model.id)}
                  className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left text-sm hover:bg-accent-50"
                >
                  <span className="truncate">
                    {model.display_name}
                    {model.id === value && <Icon name="check" size={12} />}
                  </span>
                  <span className="shrink-0 whitespace-nowrap text-[10px] text-ink-500">
                    {model.context_length ? `${Math.round(model.context_length / 1000)}k ctx` : ''}
                  </span>
                </button>
              ))}
              {!loading && openRouterResults.length === 0 && (
                <div className="px-2 py-2 text-xs text-ink-500">
                  {query.trim() ? 'No matching OpenRouter models.' : 'Type to search ~500 OpenRouter models.'}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
