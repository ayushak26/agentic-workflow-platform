import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';

/**
 * Drag-to-resize a panel's width, persisted per storageKey so it survives
 * reloads/navigation. `side` controls which edge grows the panel as the
 * pointer moves (left panel grows to the right, right panel grows to the
 * left).
 */
export function useResizablePanel({
  storageKey,
  defaultWidth,
  minWidth,
  maxWidth,
  side,
}: {
  storageKey: string;
  defaultWidth: number;
  minWidth: number;
  maxWidth: number;
  side: 'left' | 'right';
}) {
  const [width, setWidth] = useState(() => {
    const stored = Number(window.localStorage.getItem(storageKey));
    return Number.isFinite(stored) && stored > 0
      ? Math.min(maxWidth, Math.max(minWidth, stored))
      : defaultWidth;
  });
  const [dragging, setDragging] = useState(false);
  const dragStart = useRef<{ pointerX: number; width: number } | null>(null);

  useEffect(() => {
    window.localStorage.setItem(storageKey, String(width));
  }, [storageKey, width]);

  const onPointerDown = useCallback((e: ReactPointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    dragStart.current = { pointerX: e.clientX, width };
    setDragging(true);
  }, [width]);

  const onPointerMove = useCallback((e: ReactPointerEvent) => {
    if (!dragStart.current) return;
    const delta = e.clientX - dragStart.current.pointerX;
    const signedDelta = side === 'left' ? delta : -delta;
    const next = Math.min(maxWidth, Math.max(minWidth, dragStart.current.width + signedDelta));
    setWidth(next);
  }, [maxWidth, minWidth, side]);

  const onPointerUp = useCallback((e: ReactPointerEvent) => {
    dragStart.current = null;
    setDragging(false);
    e.currentTarget.releasePointerCapture(e.pointerId);
  }, []);

  return {
    width,
    setWidth,
    dragging,
    handleProps: { onPointerDown, onPointerMove, onPointerUp },
  };
}
