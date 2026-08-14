import { useEffect, useState } from 'react';
import { knowledgeApi, type EmbeddingModelChoice, type IngestionJob } from '../../api/knowledge';
import { ErrorNotice, ResourceId, Status } from './shared';
import { INGESTION_PRESETS, ingestionProfileRequests, type IngestionPresetKey } from './config';
import { useCollection } from './collectionStore';

export function IngestionPage({ onInspect, onPlayground, onAgents }: { onInspect: () => void; onPlayground: () => void; onAgents: () => void }) {
  const { collections, collectionId, collection, refresh: refreshCollections } = useCollection();
  const [preset, setPreset] = useState<IngestionPresetKey>('technical');
  const [parser, setParser] = useState<string>(INGESTION_PRESETS.technical.parser);
  const [chunker, setChunker] = useState<string>(INGESTION_PRESETS.technical.chunker);
  const [target, setTarget] = useState<number>(INGESTION_PRESETS.technical.target);
  const [max, setMax] = useState<number>(INGESTION_PRESETS.technical.max);
  const [overlap, setOverlap] = useState<number>(INGESTION_PRESETS.technical.overlap);
  const [embeddingModel, setEmbeddingModel] = useState('auto');
  const [embeddingChoices, setEmbeddingChoices] = useState<EmbeddingModelChoice[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [job, setJob] = useState<IngestionJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    if (!job) return;
    if (['completed', 'partially_completed', 'failed', 'cancelled'].includes(job.status)) {
      // Job is done — refresh the shared collection so the bar's document and
      // chunk counts reflect what was just ingested.
      void refreshCollections();
      return;
    }
    // 3s, not 1.2s: the API allows 60 requests/minute per user, and a 1.2s poll
    // spends 50 of them on its own — enough to rate-limit the rest of the Studio
    // while an ingestion is being watched.
    const timer = window.setInterval(() => knowledgeApi.getJob(job.ingestion_job_id).then(setJob).catch(error => setError(String(error))), 3000);
    return () => window.clearInterval(timer);
  }, [job, refreshCollections]);
  useEffect(() => { knowledgeApi.embeddingModels().then(r => setEmbeddingChoices(r.models)).catch(() => setEmbeddingChoices([])); }, []);
  function choosePreset(value: IngestionPresetKey) {
    const next = INGESTION_PRESETS[value]; setPreset(value); setParser(next.parser); setChunker(next.chunker); setTarget(next.target); setMax(next.max); setOverlap(next.overlap);
  }
  async function run() {
    setRunning(true); setError(null);
    try {
      const stamp = new Date().toISOString().slice(0, 16);
      const payloads = ingestionProfileRequests({ preset, parser, chunker, target, max, overlap, stamp });
      const parserProfile = await knowledgeApi.createProfile(payloads.parser);
      const chunkingProfile = await knowledgeApi.createProfile(payloads.chunking);
      // The embedding model defines the Index's vector space, so the backend
      // resolves it (including 'auto') and pins the concrete model on the Index.
      const created = await knowledgeApi.startIngestion(collectionId, files, { parser: parserProfile, chunking: chunkingProfile, embeddingModel }, { document_type: 'technical_documentation' });
      setJob(created);
    } catch (error) { setError(String(error)); } finally { setRunning(false); }
  }
  const completed = job && ['completed', 'partially_completed'].includes(job.status);
  const progress = job ? Math.round(((job.documents_processed + job.documents_failed) / Math.max(1, job.documents_total)) * 100) : 0;
  return <div className="space-y-5">
    <div className="flex flex-wrap gap-2">{['Collection', 'Add Sources', 'Parser', 'Chunking', 'Enrichment', 'Embedding', 'Index', 'Review', 'Run', 'Inspect'].map((step, index) => <span key={step} className="rounded-full border border-slate-200 bg-white px-3 py-1 text-[10px] text-ink-600"><b>{index + 1}</b> {step}</span>)}</div>
    <section className="ui-card p-5 space-y-5">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-ink-600 md:col-span-2">Documents will be added to <b className="text-ink-900">{collection?.name ?? '—'}</b>. Change the collection in the bar above.</div>
        <label className="text-xs text-ink-600">Preset<select className="ui-input mt-1 w-full" value={preset} onChange={event => choosePreset(event.target.value as IngestionPresetKey)}>{Object.entries(INGESTION_PRESETS).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>
        <label className="text-xs text-ink-600">Parser<select className="ui-input mt-1 w-full" value={parser} onChange={event => setParser(event.target.value)}><option value="standard">Standard</option><option value="layout_aware">Layout-aware</option><option value="structure_aware">Structure-aware</option><option value="vision_augmented">Vision-augmented (figures &amp; tables)</option><option value="ocr_fallback">OCR fallback (scanned PDFs)</option></select></label>
        <label className="text-xs text-ink-600">Embedding model<select className="ui-input mt-1 w-full" value={embeddingModel} onChange={event => setEmbeddingModel(event.target.value)}><option value="auto">Auto — best for this corpus</option>{embeddingChoices.map(item => <option key={item.id} value={item.id}>{item.label} · {item.dimensions}d{item.verified ? '' : ' (provider not enabled)'}</option>)}</select><span className="mt-1 block text-[11px] text-ink-400">{embeddingModel === 'auto' ? 'Chosen per corpus from document types, size and language; the resolved model is pinned on the Index.' : embeddingChoices.find(item => item.id === embeddingModel)?.note ?? ''}</span></label>
        <label className="text-xs text-ink-600">Chunking strategy<select className="ui-input mt-1 w-full" value={chunker} onChange={event => setChunker(event.target.value)}>{['fixed_token', 'recursive', 'structure_aware', 'parent_child', 'contextual', 'semantic', 'sentence_window'].map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
      </div>
      <div className="grid grid-cols-3 gap-3">
        <label className="text-xs text-ink-600">Target tokens<input type="number" className="ui-input mt-1 w-full" value={target} onChange={event => setTarget(Number(event.target.value))} /></label>
        <label className="text-xs text-ink-600">Maximum tokens<input type="number" className="ui-input mt-1 w-full" value={max} onChange={event => setMax(Number(event.target.value))} /></label>
        <label className="text-xs text-ink-600">Overlap<input type="number" className="ui-input mt-1 w-full" value={overlap} onChange={event => setOverlap(Number(event.target.value))} /></label>
      </div>
      <label className="block rounded-lg border border-dashed border-slate-300 p-5 text-center text-sm text-ink-600">Add PDF, DOCX, XLSX, PPTX, CSV, TSV, TXT, Markdown, JSON, HTML, XML or code files<span className="mt-1 block text-[11px] text-ink-400">Up to 70 MB per file, 20 files per run</span><input className="mt-3 block w-full text-xs" type="file" multiple onChange={event => setFiles(Array.from(event.target.files ?? []))} /></label>
      <div className="flex items-center justify-end gap-3">
        {collections.length === 0 && <p className="text-xs text-amber-700">Create a Collection first — ingestion needs somewhere to put the documents.</p>}
        {collections.length > 0 && files.length === 0 && <p className="text-xs text-ink-500">Choose at least one file to enable ingestion.</p>}
        <button type="button" className="ui-button ui-button--primary" disabled={running || !collectionId || files.length === 0} onClick={() => void run()}>{running ? 'Preparing ingestion…' : `Run ingestion (${files.length} files)`}</button>
      </div>
    </section>
    <ErrorNotice error={error} />
    {job && <section className="ui-card p-5">
      <div className="flex items-center justify-between"><h3 className="font-semibold">Ingestion job</h3><Status value={job.status} /></div>
      <div className="mt-3 h-2 overflow-hidden rounded bg-slate-100"><div className="h-full bg-accent-600" style={{ width: `${progress}%` }} /></div>
      <div className="mt-4 grid gap-3 sm:grid-cols-3 text-xs"><div>Processed <b>{job.documents_processed}/{job.documents_total}</b></div><div>Failed <b>{job.documents_failed}</b></div><div>Chunks <b>{job.chunks_created}</b></div></div>
      <div className="mt-4 grid gap-3 lg:grid-cols-2"><div><div className="text-[10px] uppercase text-ink-400">Ingestion Job</div><ResourceId value={job.ingestion_job_id} /></div><div><div className="text-[10px] uppercase text-ink-400">Index</div><ResourceId value={job.target_index_id} /></div><div><div className="text-[10px] uppercase text-ink-400">Parser Profile</div><ResourceId value={job.parser_profile_id} /></div><div><div className="text-[10px] uppercase text-ink-400">Chunking Profile</div><ResourceId value={job.chunking_profile_id} /></div><div><div className="text-[10px] uppercase text-ink-400">Embedding Profile</div><ResourceId value={job.embedding_profile_id} /></div><div><div className="text-[10px] uppercase text-ink-400">Collection</div><ResourceId value={job.collection_id} /></div></div>
      {completed && <div className="mt-5 flex flex-wrap gap-2"><button className="ui-button ui-button--secondary" onClick={onInspect}>Inspect chunks</button><button className="ui-button ui-button--secondary" onClick={onPlayground}>Test retrieval</button><button className="ui-button ui-button--primary" onClick={onAgents}>Create RAG Agent</button></div>}
    </section>}
  </div>;
}
