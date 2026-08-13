import { useEffect, useState } from 'react';
import { knowledgeApi, type CollectionResource, type ProfileVersion, type RAGAgentDefinition, type RAGQueryResponse } from '../../api/knowledge';
import { ErrorNotice, ResourceId, Status } from './shared';

export function ProfilesAgentsPage() {
  const [collections, setCollections] = useState<CollectionResource[]>([]);
  const [retrievalProfiles, setRetrievalProfiles] = useState<ProfileVersion[]>([]);
  const [generationProfiles, setGenerationProfiles] = useState<ProfileVersion[]>([]);
  const [routingProfiles, setRoutingProfiles] = useState<ProfileVersion[]>([]);
  const [agents, setAgents] = useState<RAGAgentDefinition[]>([]);
  const [name, setName] = useState('');
  const [collectionId, setCollectionId] = useState('');
  const [retrievalProfileId, setRetrievalProfileId] = useState('');
  const [generationProfileId, setGenerationProfileId] = useState('');
  const [routingProfileId, setRoutingProfileId] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testAgentId, setTestAgentId] = useState('');
  const [testQuery, setTestQuery] = useState('');
  const [testing, setTesting] = useState(false);
  const [response, setResponse] = useState<RAGQueryResponse | null>(null);

  function refresh() {
    Promise.all([
      knowledgeApi.listCollections(),
      knowledgeApi.listProfiles('retrieval'),
      knowledgeApi.listProfiles('generation'),
      knowledgeApi.listProfiles('routing'),
      knowledgeApi.listRagAgents(),
    ]).then(([cols, retrieval, generation, routing, ragAgents]) => {
      setCollections(cols); setRetrievalProfiles(retrieval); setGenerationProfiles(generation); setRoutingProfiles(routing); setAgents(ragAgents);
      setCollectionId(value => value || cols[0]?.collection_id || '');
      setRetrievalProfileId(value => value || retrieval[0]?.profile_id || '');
      setGenerationProfileId(value => value || generation[0]?.profile_id || '');
    }).catch(err => setError(String(err)));
  }
  useEffect(refresh, []);

  async function ensureGenerationDefault() {
    if (generationProfiles.length > 0) return;
    const defaults = await knowledgeApi.defaults();
    setGenerationProfiles([defaults.generation]);
    setGenerationProfileId(defaults.generation.profile_id);
  }

  async function create() {
    setCreating(true); setError(null);
    try {
      await ensureGenerationDefault();
      await knowledgeApi.createRagAgent({
        name, collection_id: collectionId, retrieval_profile_id: retrievalProfileId,
        generation_profile_id: generationProfileId, routing_profile_id: routingProfileId || null,
      });
      setName('');
      refresh();
    } catch (err) { setError(String(err)); } finally { setCreating(false); }
  }

  async function runTest() {
    setTesting(true); setError(null); setResponse(null);
    try { setResponse(await knowledgeApi.queryRagAgent(testAgentId, testQuery)); }
    catch (err) { setError(String(err)); } finally { setTesting(false); }
  }

  return <div className="space-y-5">
    <section className="ui-card p-5 space-y-4">
      <h3 className="font-semibold">Create RAG Agent</h3>
      <p className="text-sm text-ink-500">Binds a Collection, Retrieval Profile and Generation Profile into one saved resource a workflow can select by ID.</p>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-ink-600">Name<input className="ui-input mt-1 w-full" value={name} onChange={event => setName(event.target.value)} placeholder="Verder Product Support" /></label>
        <label className="text-xs text-ink-600">Collection<select className="ui-input mt-1 w-full" value={collectionId} onChange={event => setCollectionId(event.target.value)}>{collections.map(item => <option key={item.collection_id} value={item.collection_id}>{item.name}</option>)}</select></label>
        <label className="text-xs text-ink-600">Retrieval Profile<select className="ui-input mt-1 w-full" value={retrievalProfileId} onChange={event => setRetrievalProfileId(event.target.value)}>{retrievalProfiles.length === 0 && <option value="">Save one from the Playground first</option>}{retrievalProfiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.name} v{item.version}</option>)}</select></label>
        <label className="text-xs text-ink-600">Generation Profile<select className="ui-input mt-1 w-full" value={generationProfileId} onChange={event => setGenerationProfileId(event.target.value)}>{generationProfiles.length === 0 && <option value="">Default grounded profile</option>}{generationProfiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.name} v{item.version}</option>)}</select></label>
        <label className="text-xs text-ink-600">Routing Profile (optional)<select className="ui-input mt-1 w-full" value={routingProfileId} onChange={event => setRoutingProfileId(event.target.value)}><option value="">None — single collection</option>{routingProfiles.map(item => <option key={item.profile_id} value={item.profile_id}>{item.name} v{item.version}</option>)}</select></label>
      </div>
      <div className="flex justify-end"><button type="button" className="ui-button ui-button--primary" disabled={creating || !name.trim() || !collectionId || !retrievalProfileId} onClick={() => void create()}>{creating ? 'Creating…' : 'Create RAG Agent'}</button></div>
    </section>
    <ErrorNotice error={error} />
    <section className="ui-card p-5">
      <h3 className="mb-4 font-semibold">RAG Agents</h3>
      <div className="space-y-2">
        {agents.length === 0 && <p className="text-sm text-ink-500">No RAG Agents yet.</p>}
        {agents.map(item => <div key={item.rag_agent_id} className="grid gap-2 rounded-lg border border-slate-200 p-4 sm:grid-cols-[1fr_auto_auto] sm:items-center">
          <div><div className="flex items-center gap-2"><b>{item.name}</b><Status value={item.status} /></div>{item.description && <p className="mt-1 text-xs text-ink-500">{item.description}</p>}</div>
          <ResourceId value={item.rag_agent_id} />
          <button type="button" className="ui-button ui-button--secondary" onClick={() => setTestAgentId(item.rag_agent_id)}>Test</button>
        </div>)}
      </div>
    </section>
    {testAgentId && <section className="ui-card p-5 space-y-3">
      <div className="flex items-center justify-between"><h3 className="font-semibold">Test query</h3><ResourceId value={testAgentId} /></div>
      <textarea className="ui-input min-h-16 w-full" value={testQuery} onChange={event => setTestQuery(event.target.value)} placeholder="Ask the RAG Agent a question…" />
      <div className="flex justify-end"><button type="button" className="ui-button ui-button--primary" disabled={testing || !testQuery.trim()} onClick={() => void runTest()}>{testing ? 'Running…' : 'Run test query'}</button></div>
      {response && <div className="space-y-3 rounded-lg border border-slate-200 p-4">
        <p className="whitespace-pre-wrap text-sm">{response.answer}</p>
        <div className="flex flex-wrap gap-2 text-xs text-ink-500"><ResourceId value={response.retrieval_trace_id} /><span>{response.candidate_count} candidates · {response.context_count} in context</span></div>
        {response.citations.length > 0 && <div>
          <div className="text-[10px] uppercase text-ink-400">Citations (retrieved, not independently verified)</div>
          <div className="mt-2 space-y-1">{response.citations.map(c => <div key={`${c.label}:${c.chunk_id}`} className="rounded border border-slate-100 p-2 text-xs"><b>[{c.label}]</b> {c.filename} {c.page ? `p.${c.page}` : ''} — {c.snippet}</div>)}</div>
        </div>}
      </div>}
    </section>}
  </div>;
}
