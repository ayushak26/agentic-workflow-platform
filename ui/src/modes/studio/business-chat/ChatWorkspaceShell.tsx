import { useRef, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react';

export type ChatWorkspacePanel = 'sources' | 'chat' | 'session';

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, value));
}

export function ChatWorkspaceShell({
  sources,
  conversation,
  session,
  sourcesCollapsed,
  sessionCollapsed,
  sourcesWidth,
  sessionWidth,
  mobilePanel,
  distractionFree,
  onSourcesWidthChange,
  onSessionWidthChange,
  onMobilePanelChange,
}: {
  sources: ReactNode;
  conversation: ReactNode;
  session: ReactNode;
  sourcesCollapsed: boolean;
  sessionCollapsed: boolean;
  sourcesWidth: number;
  sessionWidth: number;
  mobilePanel: ChatWorkspacePanel;
  distractionFree: boolean;
  onSourcesWidthChange: (width: number) => void;
  onSessionWidthChange: (width: number) => void;
  onMobilePanelChange: (panel: ChatWorkspacePanel) => void;
}) {
  const dragRef = useRef<{ side: 'sources' | 'session'; startX: number; startWidth: number } | null>(null);
  const leftWidth = distractionFree || sourcesCollapsed ? 48 : sourcesWidth;
  const rightWidth = distractionFree || sessionCollapsed ? 48 : sessionWidth;
  const style = {
    '--chat-sources-width': `${leftWidth}px`,
    '--chat-session-width': `${rightWidth}px`,
  } as CSSProperties;

  function beginResize(side: 'sources' | 'session', event: ReactPointerEvent<HTMLDivElement>) {
    const startWidth = side === 'sources' ? sourcesWidth : sessionWidth;
    dragRef.current = { side, startX: event.clientX, startWidth };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function resize(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = dragRef.current;
    if (!drag) return;
    const delta = event.clientX - drag.startX;
    const next = drag.side === 'sources' ? drag.startWidth + delta : drag.startWidth - delta;
    if (drag.side === 'sources') onSourcesWidthChange(clamp(next, 240, 440));
    else onSessionWidthChange(clamp(next, 280, 520));
  }

  function finishResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragRef.current = null;
  }

  function keyboardResize(side: 'sources' | 'session', key: string) {
    if (key !== 'ArrowLeft' && key !== 'ArrowRight') return;
    const direction = key === 'ArrowRight' ? 1 : -1;
    if (side === 'sources') onSourcesWidthChange(clamp(sourcesWidth + direction * 16, 240, 440));
    else onSessionWidthChange(clamp(sessionWidth - direction * 16, 280, 520));
  }

  return (
    <>
      <div className={`chat-workspace-grid ${distractionFree ? 'is-distraction-free' : ''}`} style={style}>
        <div className={`chat-mobile-panel ${mobilePanel === 'sources' ? 'is-active' : ''}`}>{sources}</div>
        {!sourcesCollapsed && !distractionFree && (
          <div
            className="chat-resize-handle chat-resize-handle--left"
            role="separator"
            aria-label="Resize Sources panel"
            aria-orientation="vertical"
            tabIndex={0}
            onPointerDown={event => beginResize('sources', event)}
            onPointerMove={resize}
            onPointerUp={finishResize}
            onDoubleClick={() => onSourcesWidthChange(304)}
            onKeyDown={event => keyboardResize('sources', event.key)}
          />
        )}
        <div className={`chat-shell-center ${mobilePanel === 'chat' ? 'is-active' : ''}`}>{conversation}</div>
        {!sessionCollapsed && !distractionFree && (
          <div
            className="chat-resize-handle chat-resize-handle--right"
            role="separator"
            aria-label="Resize Session panel"
            aria-orientation="vertical"
            tabIndex={0}
            onPointerDown={event => beginResize('session', event)}
            onPointerMove={resize}
            onPointerUp={finishResize}
            onDoubleClick={() => onSessionWidthChange(332)}
            onKeyDown={event => keyboardResize('session', event.key)}
          />
        )}
        <div className={`chat-mobile-panel ${mobilePanel === 'session' ? 'is-active' : ''}`}>{session}</div>
      </div>
      <nav className="chat-mobile-nav" aria-label="Chat workspace panels">
        {(['sources', 'chat', 'session'] as const).map(panel => (
          <button
            type="button"
            key={panel}
            className={mobilePanel === panel ? 'is-active' : ''}
            aria-current={mobilePanel === panel ? 'page' : undefined}
            onClick={() => onMobilePanelChange(panel)}
          >
            {panel[0].toUpperCase() + panel.slice(1)}
          </button>
        ))}
      </nav>
    </>
  );
}