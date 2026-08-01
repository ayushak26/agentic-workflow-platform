import type { PointerEvent as ReactPointerEvent } from 'react';

export function ResizeHandle({
  onPointerDown,
  onPointerMove,
  onPointerUp,
  dragging,
}: {
  onPointerDown: (e: ReactPointerEvent) => void;
  onPointerMove: (e: ReactPointerEvent) => void;
  onPointerUp: (e: ReactPointerEvent) => void;
  dragging: boolean;
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      className={`flex-none w-1.5 cursor-col-resize hover:bg-accent-200 ${
        dragging ? 'bg-accent-300 select-none' : 'bg-transparent'
      }`}
      style={dragging ? { userSelect: 'none' } : undefined}
    />
  );
}
