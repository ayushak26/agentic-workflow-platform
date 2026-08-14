import { useEffect, useState } from 'react';
import { knowledgeApi, type ProfileVersion, type RetrievalPreset, type RetrievalResult } from '../../api/knowledge';
import { ErrorNotice, ResourceId } from './shared';
import { useCollection } from './collectionStore';
import { comparisonExperiments } from './config';

const STRATEGIES = ['dense', 'sparse', 'hybrid', 'hybrid_rerank'];

function ResultColumn({ title, result }: { title: string; result: RetrievalResult }) {
  return <article className="min-w-0 rounded-xl border border-slate-200 bg-white p-4">
    <div className="flex items-center justify-between"><h3 className="font-semibold">{title}</h3><span className="text-xs text-ink-500">{Math.round(result.timings_ms.total_ms ?? 0)} ms</span></div>
    <div className="mt-2"><ResourceId value={result.retrieval_request_id} /></div>
    <div className="mt-3 space-y-2">{result.chunks.map((chunk, index) => <div key={chunk.chunk_id} className="rounded border border-slate-100 p-3 text-xs"><div className="flex items-center justify-between gap-2"><b>#{index + 1} {chunk.doc_title}</b><span>page {chunk.page ?? '—'}</span></div><div className="mt-1 flex flex-wrap gap-2 text-[10px] text-ink-500"><span>dense {chunk.dense_score?.toFixed(3) ?? '—'}</span><span>BM25 {chunk.sparse_score?.toFixed(3) ?? '—'}</span><span>fusion {(chunk.fusion_score ?? chunk.hybrid_score)?.toFixed(3) ?? '—'}</span><span>rerank {chunk.rerank_score?.toFixed(3) ?? '—'}</span></div><p className="mt-2 line-clamp-4 whitespace-pre-wrap text-ink-700">{chunk.text}</p></div>)}</div>
    <details className="mt-4"><summary className="cursor-pointer text-xs font-medium text-accent-700">Exact final context ({result.context_token_count} tokens)</summary><pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-3 text-[11px] text-slate-100">{result.final_context}</pre></details>
  </article>;
}

export function PlaygroundPage({ onAgents }: { onAgents: () => void }) {
  const { collectionId, collection } = useCollection();
  const [profiles, setProfiles] = useState<ProfileVersion[]>([]);
  const [presets, setPresets] = useState<Record<string, RetrievalPreset>>({});
  const [profileId, setProfileId] = useState('');
  const [query, setQuery] = useState('How should the Dura 25 be used with sodium hypochlorite?');
  const [strategy, setStrategy] = useState('hybrid_rerank');
  const [fusion, setFusion] = useState('relative_score');
  const [transform, setTransform] = useState('none');
  const [expansion, setExpansion] = useState('none');
  const [candidateCount, setCandidateCount] = useState(20);
  const [finalCount, setFinalCount] = useState(6);
  const [filterField, setFilterField] = useState('');
  const [filterValue, setFilterValue] = useState('');
  const [result, setResult] = useState<RetrievalResult | null>(null);
  const [comparison, setComparison] = useState<RetrievalResult[]>([]);
  const [overlap, setOverlap] = useState<Array<{ left: number; right: number; shared_count: number; jaccard: number }>>([]);
  const [saved, setSaved] = useState<ProfileVersion | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => { Promise.all([knowledgeApi.listProfiles('retrieval'), knowledgeApi.retrievalPresets()]).then(([profs, values]) => { setProfiles(profs); setPresets(values); setProfileId(profs[0]?.profile_id ?? ''); }).catch(error => setError(String(error))); }, []);
  const filters = filterField && filterValue ? { logic: 'and', predicates: [{ field: filterField, operator: 'equals', value: filterValue }], groups: [] } : undefined;
  const config = { collection_id: collectionId, query, filters, strategy, candidate_count: candidateCount, final_count: finalCount, alpha: 0.5, fusion_strategy: fusion, rerank: strategy === 'hybrid_rerank', compress: true, query_transform: transform, context_expansion: expansion };
  function choosePreset(key: string) {
    if (!key) return;
    const value = presets[key];
    const next = value.config;
    setProfileId('');
    setStrategy(String(next.strategy ?? value.strategy));
    setCandidateCount(Number(next.candidate_count ?? 20));
    setFinalCount(Number(next.final_count ?? 6));
    setFusion(String(next.fusion_strategy ?? 'relative_score'));
    setTransform(String(next.query_transform ?? 'none'));
    setExpansion(String(next.context_expansion ?? 'none'));
  }
  async function run() { setLoading(true); setError(null); setComparison([]); setOverlap([]); try { setResult(await knowledgeApi.search(profileId ? { collection_id: collectionId, retrieval_profile_id: profileId, query, filters } : config)); } catch (error) { setError(String(error)); } finally { setLoading(false); } }
  async function compare() { setLoading(true); setError(null); setResult(null); try { const response = await knowledgeApi.compare(comparisonExperiments(config)); setComparison(response.results); setOverlap(response.pairwise_overlap); } catch (error) { setError(String(error)); } finally { setLoading(false); } }
  async function save() { try { const profile = await knowledgeApi.createProfile({ profile_type: 'retrieval', name: `Playground ${strategy.replaceAll('_', ' ')}`, strategy, config: { strategy, candidate_count: candidateCount, final_count: finalCount, alpha: 0.5, fusion_strategy: fusion, reranking_enabled: strategy === 'hybrid_rerank', compression_enabled: true, query_transform: transform, context_expansion: expansion } }); setSaved(profile); setProfiles(await knowledgeApi.listProfiles('retrieval')); setProfileId(profile.profile_id); } catch (error) { setError(String(error)); } }
  return <div className="space-y-5">
    <section className="ui-card p-5 space-y-4"><div className="grid gap-3 lg:grid-cols-4"><div className="text-xs">Searching<div className="ui-input mt-1 w-full truncate bg-slate-50 font-medium" title={collection?.name ?? ''}>{collection?.name ?? 'no collection'}</div></div><label className="text-xs">Saved Retrieval Profile<select className="ui-input mt-1 w-full" value={profileId} onChange={event => setProfileId(event.target.value)}><option value="">Unsaved experiment</option>{profiles.map(item => <option key={`${item.profile_id}:${item.version}`} value={item.profile_id}>{item.name} v{item.version}</option>)}</select></label><label className="text-xs">Preset<select className="ui-input mt-1 w-full" defaultValue="" onChange={event => choosePreset(event.target.value)}><option value="">Choose editable preset</option>{Object.entries(presets).map(([key, value]) => <option key={key} value={key}>{value.name}</option>)}</select></label><label className="text-xs">Strategy<select className="ui-input mt-1 w-full" value={strategy} onChange={event => { setStrategy(event.target.value); setProfileId(''); }}>{STRATEGIES.map(value => <option key={value}>{value}</option>)}</select></label></div>
      <textarea className="ui-input min-h-20 w-full" value={query} onChange={event => setQuery(event.target.value)} />
      <div className="grid gap-3 md:grid-cols-4"><label className="text-xs">Fusion<select className="ui-input mt-1 w-full" value={fusion} onChange={event => { setFusion(event.target.value); setProfileId(''); }}><option value="relative_score">Relative score</option><option value="rrf">RRF</option></select></label><label className="text-xs">Query transformation<select className="ui-input mt-1 w-full" value={transform} onChange={event => { setTransform(event.target.value); setProfileId(''); }}>{['none', 'multi_query', 'decomposition', 'hyde', 'self_query'].map(value => <option key={value}>{value}</option>)}</select></label><label className="text-xs">Context expansion<select className="ui-input mt-1 w-full" value={expansion} onChange={event => { setExpansion(event.target.value); setProfileId(''); }}>{['none', 'parent', 'sentence_window', 'contextual'].map(value => <option key={value}>{value}</option>)}</select></label><div className="grid grid-cols-2 gap-2"><label className="text-xs">Candidates<input className="ui-input mt-1 w-full" type="number" value={candidateCount} onChange={event => setCandidateCount(Number(event.target.value))} /></label><label className="text-xs">Final<input className="ui-input mt-1 w-full" type="number" value={finalCount} onChange={event => setFinalCount(Number(event.target.value))} /></label></div></div>
      <div className="grid gap-3 md:grid-cols-2"><input className="ui-input" placeholder="Metadata field, e.g. product" value={filterField} onChange={event => setFilterField(event.target.value)} /><input className="ui-input" placeholder="Filter value" value={filterValue} onChange={event => setFilterValue(event.target.value)} /></div>
      <div className="flex flex-wrap gap-2"><button className="ui-button ui-button--primary" onClick={() => void run()} disabled={loading || !collectionId}>{loading ? 'Running…' : 'Run retrieval'}</button><button className="ui-button ui-button--secondary" onClick={() => void compare()} disabled={loading || !collectionId}>Compare Dense vs Hybrid vs Reranker</button><button className="ui-button ui-button--secondary" onClick={() => void save()} disabled={Boolean(profileId)}>Save experiment as profile</button></div>
    </section>
    <ErrorNotice error={error} />
    {saved && <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-4 text-sm"><b>Saved: {saved.name}</b><div className="mt-2"><ResourceId value={saved.profile_id} /></div><button className="mt-3 text-accent-700 hover:underline" onClick={onAgents}>Create RAG Agent →</button></div>}
    {result && <div><StageTimeline result={result} /><div className="mt-4"><ResultColumn title={result.strategy} result={result} /></div></div>}
    {comparison.length > 0 && <><section className="ui-card p-4"><h3 className="font-semibold">Chunk overlap</h3><div className="mt-3 flex flex-wrap gap-2">{overlap.map(item => <span key={`${item.left}:${item.right}`} className="rounded-full bg-slate-100 px-3 py-1 text-xs">{['Dense', 'Hybrid', 'Hybrid + Reranker'][item.left]} ↔ {['Dense', 'Hybrid', 'Hybrid + Reranker'][item.right]}: {item.shared_count} shared · {(item.jaccard * 100).toFixed(0)}%</span>)}</div></section><div className="grid gap-4 xl:grid-cols-3">{comparison.map((item, index) => <ResultColumn key={item.retrieval_request_id} title={['Dense', 'Hybrid', 'Hybrid + Reranker'][index]} result={item} />)}</div></>}
  </div>;
}

function StageTimeline({ result }: { result: RetrievalResult }) {
  return <section className="ui-card p-4"><h3 className="font-semibold">Stage-by-stage retrieval</h3><div className="mt-3 grid gap-2 md:grid-cols-3">{result.stages.map(stage => <details key={stage.name} className="rounded border border-slate-200 p-3"><summary className="cursor-pointer text-xs font-medium">{stage.name.replaceAll('_', ' ')} <span className="text-ink-400">{Math.round(stage.duration_ms)} ms · {stage.output_count ?? '—'} results</span></summary><pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap text-[10px]">{JSON.stringify(stage.details, null, 2)}</pre></details>)}</div></section>;
}
