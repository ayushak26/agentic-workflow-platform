import { useEffect, useMemo, useRef, useState } from 'react';
import type { Node } from 'reactflow';
import { matchNodes } from './node-search';
import type { WorkflowNodeData } from '../yaml-bridge';

/**
 * Jump straight to a step by name (⌘K / Ctrl-K).
 *
 * On a workflow long enough to be a problem, finding "the step that emails the
 * customer" by panning is slower than typing four letters of its name — and
 * unlike panning, it works when the step is nowhere near the current viewport.
 */

export function NodeSearchPalette({
  nodes,
  onClose,
  onSelect,
}: {
  nodes: Node<WorkflowNodeData>[];
  onClose: () => void;
  onSelect: (nodeId: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const matches = useMemo(() => matchNodes(nodes, query), [nodes, query]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const active = matches[Math.min(activeIndex, matches.length - 1)];

  return (
    <div className="builder-search-backdrop" onMouseDown={onClose} role="presentation">
      <div
        className="builder-search-panel"
        onMouseDown={event => event.stopPropagation()}
        role="dialog"
        aria-label="Find a step"
        aria-modal="true"
      >
        <input
          className="builder-search-input"
          onChange={event => { setQuery(event.target.value); setActiveIndex(0); }}
          onKeyDown={event => {
            if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
              event.preventDefault();
              setActiveIndex(current => {
                const next = current + (event.key === 'ArrowDown' ? 1 : -1);
                if (matches.length === 0) return 0;
                return (next + matches.length) % matches.length;
              });
              return;
            }
            if (event.key === 'Enter' && active) {
              event.preventDefault();
              onSelect(active.id);
              return;
            }
            if (event.key === 'Escape') {
              event.preventDefault();
              onClose();
            }
          }}
          placeholder="Find a step by name, type or connected system…"
          ref={inputRef}
          value={query}
        />
        <ul className="builder-search-results">
          {matches.length === 0 && (
            <li className="builder-search-empty">No step matches “{query}”.</li>
          )}
          {matches.map((match, index) => (
            <li key={match.id}>
              <button
                className={`builder-search-result ${match.id === active?.id ? 'builder-search-result--active' : ''}`}
                onClick={() => onSelect(match.id)}
                onMouseEnter={() => setActiveIndex(index)}
                type="button"
              >
                <span className="min-w-0 flex-1 truncate font-medium text-ink-900">{match.label}</span>
                <span className="flex-none truncate text-[11px] text-ink-500">{match.detail}</span>
                {match.hasIssue && <span className="flex-none font-bold text-red-600">!</span>}
              </button>
            </li>
          ))}
        </ul>
        <div className="builder-search-hint">
          ↑↓ to choose · Enter to focus the step · Esc to close
        </div>
      </div>
    </div>
  );
}
