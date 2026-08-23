import { useMemo, useState } from 'react';
import { sourceKindLabel, sourceSelectable, sourceStatusLabel, type WorkspaceNote, type WorkspaceSource } from './chatWorkspaceModel';

type SourceFilter = 'all' | 'active' | 'referenced' | 'attention';

function sourceIcon(source: WorkspaceSource): string {
  if (source.kind === 'web') return '⌁';
  if (source.kind === 'collection') return '▦';
  if (source.kind === 'drive') return '◇';
  if (source.kind === 'image') return '▧';
  if (source.kind === 'code' || source.kind === 'repository') return '</>';
  if (source.kind === 'upload') return '↑';
  return '▤';
}

export function NotebookSourcesPanel({
  sources, notes, collapsed, loading, highlightedSourceId, onCollapse, onToggle, onToggleAll, onAddSources, onOpenSource, onShowUsage, onFilesDropped, onOpenNote, onNewNote,
}: {
  sources: WorkspaceSource[];
  notes: WorkspaceNote[];
  collapsed: boolean;
  loading: boolean;
  highlightedSourceId?: string | null;
  onCollapse: () => void;
  onToggle: (sourceId: string) => void;
  onToggleAll?: (selected: boolean) => void;
  onAddSources: () => void;
  onOpenSource?: (source: WorkspaceSource) => void;
  onShowUsage?: (source: WorkspaceSource) => void;
  onFilesDropped?: (files: File[]) => void;
  onOpenNote: (note: WorkspaceNote) => void;
  onNewNote: () => void;
}) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<SourceFilter>('all');
  const [dragging, setDragging] = useState(false);
  const visible = useMemo(() => sources.filter(source => {
    const matchesQuery = `${source.title} ${source.subtitle ?? ''} ${sourceKindLabel(source.kind)}`.toLowerCase().includes(query.toLowerCase());
    const matchesFilter = filter === 'all'
      || (filter === 'active' && source.selected)
      || (filter === 'referenced' && source.referenced)
      || (filter === 'attention' && ['failed', 'outdated', 'unavailable'].includes(source.status));
    return matchesQuery && matchesFilter;
  }), [filter, query, sources]);
  if (collapsed) {
    return <aside className="chat-rail chat-rail--collapsed"><button type="button" onClick={onCollapse} aria-label="Expand sources" title="Expand sources">›</button><span>Sources</span></aside>;
  }
  const selected = sources.filter(source => source.selected).length;
  return (
    <aside className={`chat-rail chat-sources ${dragging ? 'is-drop-target' : ''}`} aria-label="Sources panel" onDragEnter={event => { if (onFilesDropped && event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDragging(true); } }} onDragOver={event => { if (onFilesDropped) event.preventDefault(); }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }} onDrop={event => { if (!onFilesDropped) return; event.preventDefault(); setDragging(false); onFilesDropped(Array.from(event.dataTransfer.files)); }}>
      <div className="chat-rail-header">
        <div><h2>Sources</h2><p>{sources.length} available · {selected} active</p></div>
        <button type="button" onClick={onCollapse} aria-label="Collapse sources">‹</button>
      </div>
      <div className="chat-rail-actions">
        <button type="button" className="chat-primary-small" onClick={onAddSources}>+ Add source</button>
        <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search sources…" aria-label="Search sources" />
        <div className="chat-source-filters" aria-label="Source filters">
          {(['all', 'active', 'referenced', 'attention'] as const).map(value => <button type="button" key={value} className={filter === value ? 'is-active' : ''} onClick={() => setFilter(value)}>{value[0].toUpperCase() + value.slice(1)}</button>)}
        </div>
        {sources.length > 0 && onToggleAll && <button type="button" className="chat-source-select-all" onClick={() => onToggleAll(selected !== sources.filter(sourceSelectable).length)}>{selected === sources.filter(sourceSelectable).length ? 'Clear active context' : 'Select all ready'}</button>}
      </div>
      <div className="chat-source-list">
        {loading && <p className="chat-muted">Loading sources…</p>}
        {!loading && visible.length === 0 && <div className="chat-empty-rail"><p>No sources yet.</p><button type="button" onClick={onAddSources}>Add your first source</button></div>}
        {dragging && <div className="chat-source-drop-message">Drop files to add them to context</div>}
        {visible.map(source => <div key={source.id} className={`chat-source-row ${source.selected ? 'is-selected' : ''} ${highlightedSourceId === source.id ? 'is-highlighted' : ''} ${onShowUsage ? 'has-usage-action' : ''}`}>
          <input aria-label={`Use ${source.title} in the next prompt`} type="checkbox" checked={source.selected} disabled={!sourceSelectable(source)} onChange={() => onToggle(source.id)} />
          <button type="button" className="chat-source-open" onClick={() => onOpenSource?.(source)}>
            <span className="chat-source-icon" aria-hidden>{sourceIcon(source)}</span>
            <span className="chat-source-copy"><span className="chat-source-title"><strong>{source.title}</strong>{source.referenced && <em>Referenced</em>}</span><small>{source.subtitle || sourceKindLabel(source.kind)}</small><span className={`chat-source-status is-${source.status}`}>{sourceStatusLabel(source)}</span></span>
          </button>
          {onShowUsage && <button type="button" className="chat-source-usage" aria-label={`Show usage for ${source.title}`} onClick={() => onShowUsage(source)}>↗</button>}
        </div>)}
      </div>
      <div className="chat-notes-section">
        <div className="chat-section-heading"><span>Notes</span><button type="button" onClick={onNewNote} aria-label="Create note">+</button></div>
        {notes.length === 0 && <p className="chat-muted">Save useful answers and excerpts here.</p>}
        {notes.map(note => <button key={note.id} type="button" className="chat-note-row" onClick={() => onOpenNote(note)}><span aria-hidden>★</span><span>{note.title}</span></button>)}
      </div>
    </aside>
  );
}