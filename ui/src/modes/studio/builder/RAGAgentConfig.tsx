import { useEffect, useRef, useState } from 'react';

import { knowledgeApi, type RAGAgentDefinition } from '../../../api/knowledge';
import type { ContractField, LLMModelInfo, OutputContract } from '../../../api/types';
import { Icon } from '../../../components/ui/Icon';
import { resolveBinding } from './binding';
import { ValuePicker } from './FieldPicker';

/**
 * Configuring a RAG Agent workflow step.
 *
 * The node is a thin interface over a RAG Agent already built in Knowledge
 * Studio — this panel picks which one and what question to give it. It does
 * not duplicate retrieval profile / answering model / prompt configuration;
 * those stay owned by Knowledge Studio (see the read-only Agent Details
 * section) so an agent can be improved there without touching any workflow
 * that uses it.
 */

type Config = Record<string, unknown>;

function asString(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export function RAGAgentConfig({
  config,
  contract,
  llmModels,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  llmModels: LLMModelInfo[];
  onChange: (next: Config) => void;
}) {
  const set = (patch: Config) => onChange({ ...config, ...patch });
  const ragAgentId = asString(config.rag_agent_id);

  return (
    <div>
      <section>
        <div className="builder-panel-heading">Agent</div>
        <AgentPicker
          ragAgentId={ragAgentId}
          onSelect={agent => set({ rag_agent_id: agent.rag_agent_id, rag_agent_name: agent.name })}
          onResolvedName={name => {
            // Refresh the cosmetic canvas-subtitle cache whenever the live
            // name is fetched (e.g. after a rename in Knowledge Studio) —
            // never the source of truth, rag_agent_id always is.
            if (name !== asString(config.rag_agent_name)) set({ rag_agent_name: name });
          }}
        />
      </section>

      <AgentDetails ragAgentId={ragAgentId} llmModels={llmModels} />

      <QuerySection config={config} contract={contract} onChange={onChange} />

      <RuntimeContextSection config={config} contract={contract} onChange={onChange} />
    </div>
  );
}

function AgentPicker({
  ragAgentId,
  onSelect,
  onResolvedName,
}: {
  ragAgentId: string;
  onSelect: (agent: RAGAgentDefinition) => void;
  onResolvedName: (name: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<RAGAgentDefinition[]>([]);
  const [loading, setLoading] = useState(false);
  const [resolved, setResolved] = useState<RAGAgentDefinition | null>(null);
  const [resolveState, setResolveState] = useState<'idle' | 'loading' | 'ok' | 'not_found'>('idle');
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!ragAgentId) {
        setResolved(null);
        setResolveState('idle');
        return;
      }
      setResolveState('loading');
      try {
        const agent = await knowledgeApi.getRagAgent(ragAgentId);
        if (cancelled) return;
        setResolved(agent);
        setResolveState('ok');
        onResolvedName(agent.name);
      } catch {
        if (!cancelled) { setResolved(null); setResolveState('not_found'); }
      }
    })();
    return () => { cancelled = true; };
    // onResolvedName intentionally excluded — it's a stable-enough closure
    // over the latest config/set, and including it would refetch on every
    // parent render instead of only when the selected agent id changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ragAgentId]);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      knowledgeApi.listRagAgents(query.trim() || undefined)
        .then(agents => { if (!cancelled) setResults(agents); })
        .catch(() => { if (!cancelled) setResults([]); })
        .finally(() => { if (!cancelled) setLoading(false); });
    }, 200);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [open, query]);

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

  const choose = (agent: RAGAgentDefinition) => {
    onSelect(agent);
    setResolved(agent);
    setResolveState('ok');
    setQuery('');
    setOpen(false);
  };

  return (
    <div ref={containerRef} className="relative mt-2">
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        className="flex w-full items-center justify-between rounded-md border border-slate-300 bg-white px-2 py-2 text-left text-sm"
      >
        <span className="truncate">
          {resolveState === 'loading' && 'Loading…'}
          {resolveState === 'not_found' && (
            <span className="text-bad">RAG Agent no longer available</span>
          )}
          {resolveState === 'ok' && resolved && resolved.name}
          {resolveState === 'idle' && 'No agent selected'}
        </span>
        <Icon name={open ? 'chevron-left' : 'chevron-right'} size={12} />
      </button>

      {resolveState === 'not_found' && (
        <p className="mt-1 text-[11px] text-bad">
          The selected RAG Agent could not be found or is no longer accessible. Choose another one.
        </p>
      )}

      {open && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-slate-200 bg-white shadow-lg">
          <div className="flex items-center gap-2 border-b border-slate-100 p-2">
            <Icon name="search" size={13} />
            <input
              autoFocus
              value={query}
              onChange={event => setQuery(event.target.value)}
              placeholder="Search RAG agents by name…"
              className="w-full border-none text-sm focus:outline-none"
            />
          </div>
          <div className="max-h-72 overflow-y-auto">
            {loading && (
              <div className="px-2 py-2 text-xs text-ink-500">Searching…</div>
            )}
            {!loading && results.length === 0 && (
              <div className="px-2 py-2 text-xs text-ink-500">
                {query.trim() ? 'No matching RAG agents.' : 'No RAG agents yet — create one in Knowledge Studio.'}
              </div>
            )}
            {results.map(agent => (
              <button
                key={agent.rag_agent_id}
                type="button"
                onClick={() => choose(agent)}
                className="flex w-full items-center justify-between px-2 py-1.5 text-left text-sm hover:bg-accent-50"
              >
                <span className="truncate">{agent.name}</span>
                {agent.rag_agent_id === ragAgentId && <Icon name="check" size={12} />}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function AgentDetails({ ragAgentId, llmModels }: { ragAgentId: string; llmModels: LLMModelInfo[] }) {
  const [collectionName, setCollectionName] = useState<string | null>(null);
  const [retrievalProfileName, setRetrievalProfileName] = useState<string | null>(null);
  const [modelLabel, setModelLabel] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setCollectionName(null);
      setRetrievalProfileName(null);
      setModelLabel(null);
      if (!ragAgentId) return;
      const agent = await knowledgeApi.getRagAgent(ragAgentId);
      const [collection, retrieval, generation] = await Promise.allSettled([
        knowledgeApi.getCollection(agent.collection_id),
        knowledgeApi.getProfile(agent.retrieval_profile_id),
        knowledgeApi.getProfile(agent.generation_profile_id),
      ]);
      if (cancelled) return;
      if (collection.status === 'fulfilled') setCollectionName(collection.value.name);
      if (retrieval.status === 'fulfilled') setRetrievalProfileName(retrieval.value.name);
      if (generation.status === 'fulfilled') {
        const model = String(generation.value.config?.model ?? 'auto');
        const known = llmModels.find(item => item.name === model);
        setModelLabel(known?.display_name ?? model);
      }
    })().catch(() => undefined);
    return () => { cancelled = true; };
  }, [ragAgentId, llmModels]);

  if (!ragAgentId) return null;

  return (
    <section className="mt-4 rounded-lg border border-slate-200 p-3">
      <div className="builder-panel-heading">Agent Details</div>
      <dl className="mt-2 space-y-1.5 text-[11px]">
        <div className="flex justify-between gap-2">
          <dt className="text-ink-500">Knowledge</dt>
          <dd className="truncate font-medium text-ink-800">{collectionName ?? '…'}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-500">Retrieval profile</dt>
          <dd className="truncate font-medium text-ink-800">{retrievalProfileName ?? '…'}</dd>
        </div>
        <div className="flex justify-between gap-2">
          <dt className="text-ink-500">Answering model</dt>
          <dd className="truncate font-medium text-ink-800">{modelLabel ?? '…'}</dd>
        </div>
      </dl>
      <p className="mt-2 text-[11px] text-ink-500">
        Configured in Knowledge Studio → Profiles &amp; RAG Agents.
      </p>
    </section>
  );
}

function QuerySection({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const [picking, setPicking] = useState(false);
  const binding = resolveBinding(config.query, contract);
  const isEmpty = binding.kind === 'empty';

  return (
    <section className="mt-4">
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-ink-700">Query</label>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setPicking(value => !value)}
          type="button"
        >
          {picking ? 'Close picker' : 'Pick a value'}
        </button>
      </div>
      <input
        className="builder-field mt-1 font-mono"
        onChange={event => onChange({ ...config, query: event.target.value })}
        placeholder='"What was our Q3 revenue?" or {{outputs.previous_step.output}}'
        value={asString(config.query)}
      />
      {isEmpty && (
        <p className="mt-1 text-[11px] text-amber-700">RAG Agent requires a query.</p>
      )}
      {picking && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <ValuePicker
            contract={contract}
            destinationKind="text"
            destinationLabel="Query"
            onPick={(field: ContractField) => {
              onChange({ ...config, query: field.reference });
              setPicking(false);
            }}
            selectedReference={asString(config.query)}
          />
        </div>
      )}
      <p className="mt-1 text-[11px] text-ink-500">
        Connected from a previous step by default. Type a fixed question instead if this step should
        always ask the same thing.
      </p>
    </section>
  );
}

function RuntimeContextSection({
  config,
  contract,
  onChange,
}: {
  config: Config;
  contract: OutputContract | null;
  onChange: (next: Config) => void;
}) {
  const [picking, setPicking] = useState(false);
  const raw = config.runtime_context;
  const referenceValue = typeof raw === 'string' ? raw : '';
  const objectValue = asRecord(raw);

  return (
    <section className="mt-4">
      <div className="flex items-center justify-between">
        <label className="text-[11px] font-medium text-ink-700">
          Runtime Context <span className="font-normal text-ink-400">Optional</span>
        </label>
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setPicking(value => !value)}
          type="button"
        >
          {picking ? 'Close picker' : 'Map previous node output'}
        </button>
      </div>
      {objectValue && (
        <div className="mt-1 rounded-md border border-ink-100 bg-brand-softer px-2 py-1.5 text-[11px] text-ink-700">
          {Object.keys(objectValue).length} field(s) mapped
        </div>
      )}
      {referenceValue && (
        <div className="mt-1 break-all rounded bg-slate-50 px-2 py-1 font-mono text-[10px] text-ink-600">
          {referenceValue}
        </div>
      )}
      {picking && (
        <div className="mt-2 rounded border border-slate-200 p-2">
          <ValuePicker
            contract={contract}
            destinationKind="any"
            destinationLabel="Runtime Context"
            destinationHint="Extra info to hand the model alongside the query — not searched as knowledge."
            onPick={(field: ContractField) => {
              onChange({ ...config, runtime_context: field.reference });
              setPicking(false);
            }}
            selectedReference={referenceValue || undefined}
          />
        </div>
      )}
      {!referenceValue && !objectValue && (
        <p className="mt-1 text-[11px] text-ink-500">
          Extra info to accompany the query — e.g. a customer record from an earlier step. Never
          indexed as knowledge, kept separate from what the agent retrieves.
        </p>
      )}
    </section>
  );
}
