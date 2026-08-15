import { useEffect, useState } from 'react';
import { CollectionsPage } from './CollectionsPage';
import { DocumentsIndexesPage } from './DocumentsIndexesPage';
import { IngestionPage } from './IngestionPage';
import { PlaygroundPage } from './PlaygroundPage';
import { ProfilesAgentsPage } from './ProfilesAgentsPage';
import { TracesPage } from './TracesPage';
import { CollectionBar, CollectionProvider } from './CollectionContext';
import { useCollection } from './collectionStore';

type Tab = 'collections' | 'ingestion' | 'documents' | 'playground' | 'agents' | 'traces';
const TABS: Array<[Tab, string]> = [['collections', 'Collections'], ['ingestion', 'Ingestion'], ['documents', 'Documents & Indexes'], ['playground', 'Retrieval Playground'], ['agents', 'Profiles & RAG Agents'], ['traces', 'Retrieval Traces']];

/** Tab content, remounted whenever the selected collection changes so no page
 *  can show a previous collection's documents, indexes or results. */
function KnowledgeTabs({ tab, setTab }: { tab: Tab; setTab: (value: Tab) => void }) {
  const { collectionId } = useCollection();
  // Not on 'collections': that tab *is* the create form and its own list, so the
  // bar would duplicate it — and its empty-state button would only switch to the
  // tab already open, i.e. a button that does nothing.
  const showBar = tab !== 'traces' && tab !== 'collections';
  return <>
    {showBar && <CollectionBar onCreate={() => setTab('collections')} />}
    <div key={collectionId}>
      {tab === 'collections' && <CollectionsPage />}
      {tab === 'ingestion' && <IngestionPage onInspect={() => setTab('documents')} onPlayground={() => setTab('playground')} onAgents={() => setTab('agents')} />}
      {tab === 'documents' && <DocumentsIndexesPage />}
      {tab === 'playground' && <PlaygroundPage onAgents={() => setTab('agents')} />}
      {tab === 'agents' && <ProfilesAgentsPage />}
      {tab === 'traces' && <TracesPage />}
    </div>
  </>;
}

export function KnowledgeRoot() {
  const [tab, setTab] = useState<Tab>(() => window.localStorage.getItem('eurskem.knowledge.trace') ? 'traces' : 'collections');
  useEffect(() => {
    const handler = () => setTab('traces');
    window.addEventListener('eurskem:open-knowledge-trace', handler);
    return () => window.removeEventListener('eurskem:open-knowledge-trace', handler);
  }, []);
  return <CollectionProvider>
    <div className="p-5 lg:p-7">
      <div className="mb-5">
        <h1 className="text-xl font-semibold text-ink-900">Knowledge Studio</h1>
        <p className="mt-1 text-sm text-ink-500">Manage what knowledge exists, how it is prepared, how it is searched, and how grounded answers are produced.</p>
      </div>
      <div className="mb-5 flex flex-wrap gap-2">{TABS.map(([key, label]) => <button type="button" key={key} className={`ui-button ${tab === key ? 'ui-button--primary' : 'ui-button--secondary'}`} onClick={() => setTab(key)}>{label}</button>)}</div>
      {/* Every tab acts on the collection named in the bar — shown once, always. */}
      <KnowledgeTabs tab={tab} setTab={setTab} />
    </div>
  </CollectionProvider>;
}
