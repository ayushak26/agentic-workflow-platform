import { useEffect, useRef, useState } from 'react';
import type { WorkflowSummary } from '../../../api/types';
import { Icon } from '../../../components/ui/Icon';
import { humanizeIdentifier } from '../guided/runtime-model';
import { READINESS_LABEL } from './readiness';

const STATUS_LABEL: Record<string, string> = {
  approved: 'Approved',
  draft: 'Draft',
  in_review: 'In review',
  deprecated: 'Deprecated',
  archived: 'Archived',
};

function durationText(workflow: WorkflowSummary): string {
  const duration = workflow.library?.typical_duration;
  if (!duration || (duration.minimum_minutes == null && duration.maximum_minutes == null)) {
    return 'Duration unknown';
  }
  const { minimum_minutes: min, maximum_minutes: max } = duration;
  if (min != null && max != null && min !== max) return `${min}–${max} min`;
  return `${max ?? min} min`;
}

export function WorkflowCard({
  workflow,
  favorite,
  selected,
  onSelect,
  onToggleFavorite,
  onOpenBuilder,
  onPrepareRun,
  onDelete,
}: {
  workflow: WorkflowSummary;
  favorite: boolean;
  selected: boolean;
  onSelect: () => void;
  onToggleFavorite: () => void;
  onOpenBuilder: () => void;
  onPrepareRun: () => void;
  onDelete: () => void;
}) {
  const title = workflow.library?.title || humanizeIdentifier(workflow.name);
  const summary = workflow.library?.summary || workflow.description || 'Description not yet provided.';
  const status = workflow.library?.visibility_status ?? 'draft';
  const reviewCount = workflow.library?.human_reviews.count ?? 0;
  const outputs = workflow.library?.outputs ?? [];
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onPointerDown(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    }
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [menuOpen]);

  return (
    <article
      className={`library-card ${selected ? 'is-selected' : ''}`}
      aria-current={selected ? 'true' : undefined}
    >
      <div className="library-card-corner-actions">
        <button
          type="button"
          className="library-card-favorite"
          aria-pressed={favorite}
          aria-label={favorite ? `Remove ${title} from favorites` : `Add ${title} to favorites`}
          onClick={event => { event.stopPropagation(); onToggleFavorite(); }}
        >
          <Icon name={favorite ? 'star-filled' : 'star'} size={16} />
        </button>
        <div className="library-card-menu" ref={menuRef}>
          <button
            type="button"
            className="library-card-favorite"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label={`More actions for ${title}`}
            onClick={event => { event.stopPropagation(); setMenuOpen(value => !value); }}
          >
            <Icon name="more-vertical" size={16} />
          </button>
          {menuOpen && (
            <div className="library-card-menu-popover" role="menu">
              <button
                type="button"
                role="menuitem"
                className="library-card-menu-item is-destructive"
                onClick={event => { event.stopPropagation(); setMenuOpen(false); onDelete(); }}
              >
                <Icon name="trash" size={14} /> Delete workflow
              </button>
            </div>
          )}
        </div>
      </div>

      <button type="button" className="library-card-main" onClick={onSelect}>
        <h3 className="library-card-title" title={title}>{title}</h3>
        <p className="library-card-summary">{summary}</p>

        <div className="library-card-chips">
          <span className={`library-status-chip is-${status}`}>{STATUS_LABEL[status] ?? status}</span>
          <span className={`library-readiness-chip is-${workflow.readiness.level}`}>
            <span aria-hidden="true">
              {workflow.readiness.level === 'ready' ? '●' : workflow.readiness.level === 'blocked' ? '×' : '!'}
            </span>
            {READINESS_LABEL[workflow.readiness.level]}
          </span>
          {outputs.map(output => (
            <span className="library-output-chip" key={output}>{output.toUpperCase()}</span>
          ))}
        </div>

        <div className="library-card-meta">
          <span>{durationText(workflow)}</span>
          {reviewCount > 0 && (
            <span>{reviewCount} review point{reviewCount === 1 ? '' : 's'}</span>
          )}
          <span>{workflow.node_count} step{workflow.node_count === 1 ? '' : 's'}</span>
        </div>
      </button>

      <div className="library-card-actions">
        <button
          type="button"
          className="ui-button ui-button--primary"
          disabled={workflow.readiness.level === 'blocked'}
          onClick={event => { event.stopPropagation(); onPrepareRun(); }}
        >
          Prepare and run
        </button>
        <button
          type="button"
          className="ui-button ui-button--secondary"
          onClick={event => { event.stopPropagation(); onOpenBuilder(); }}
        >
          Open in Builder
        </button>
      </div>
    </article>
  );
}
