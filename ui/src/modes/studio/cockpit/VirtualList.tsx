// A small fixed-row-height windowed list. No external dependency —
// react-window would be overkill for the few-hundred-row lists this app
// deals with (workflow node lists, per-node log lines), but rendering
// every row unconditionally still costs real layout/paint time once a
// workflow has hundreds of nodes, so we window anyway.
import { useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';

const OVERSCAN = 4;

export function VirtualList<T>({
  items,
  itemHeight,
  height,
  renderItem,
  emptyState,
  className = '',
}: {
  items: T[];
  itemHeight: number;
  // Fixed pixel height, or omit to fill the parent (measured via
  // ResizeObserver) — the common case inside a flex-1 panel where the
  // available height isn't known ahead of time.
  height?: number;
  renderItem: (item: T, index: number) => ReactNode;
  emptyState?: ReactNode;
  className?: string;
}) {
  const [scrollTop, setScrollTop] = useState(0);
  const [measuredHeight, setMeasuredHeight] = useState(height ?? 0);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (height != null) return;
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry) setMeasuredHeight(entry.contentRect.height);
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [height]);

  const effectiveHeight = height ?? measuredHeight;

  if (items.length === 0 && emptyState) {
    return <div ref={containerRef} className={className} style={{ height }}>{emptyState}</div>;
  }

  const visibleCount = Math.ceil(effectiveHeight / itemHeight);
  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - OVERSCAN);
  const endIndex = Math.min(items.length, startIndex + visibleCount + OVERSCAN * 2);
  const totalHeight = items.length * itemHeight;
  const offsetY = startIndex * itemHeight;

  const innerStyle: CSSProperties = { height: totalHeight, position: 'relative' };
  const windowStyle: CSSProperties = {
    position: 'absolute', top: offsetY, left: 0, right: 0,
  };

  return (
    <div
      ref={containerRef}
      className={`overflow-y-auto ${className}`}
      style={{ height }}
      onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
    >
      <div style={innerStyle}>
        <div style={windowStyle}>
          {items.slice(startIndex, endIndex).map((item, i) => (
            <div key={startIndex + i} style={{ height: itemHeight }}>
              {renderItem(item, startIndex + i)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
