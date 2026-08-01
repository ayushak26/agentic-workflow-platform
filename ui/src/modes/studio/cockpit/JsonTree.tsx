/* Runtime node payloads are intentionally plugin-defined and heterogeneous. */
/* eslint-disable @typescript-eslint/no-explicit-any */
// A small recursive, collapsible, searchable JSON tree — no external
// dependency. Used by the inspector's Output/Input/Metadata tabs so large
// structured output can be explored without dumping a giant pretty-printed
// string into the panel (which is what NodeCard in RunHistory.tsx still
// does for its simpler use case).
import { useMemo, useState } from 'react';

type JsonValue = any;

function valueMatches(value: JsonValue, needle: string): boolean {
  if (needle === '') return true;
  const lower = needle.toLowerCase();
  if (value == null) return false;
  if (typeof value === 'string') return value.toLowerCase().includes(lower);
  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value).toLowerCase().includes(lower);
  }
  if (Array.isArray(value)) return value.some((v) => valueMatches(v, needle));
  if (typeof value === 'object') {
    return Object.entries(value).some(
      ([k, v]) => k.toLowerCase().includes(lower) || valueMatches(v, needle),
    );
  }
  return false;
}

function Leaf({ value }: { value: JsonValue }) {
  if (value === null) return <span className="text-ink-400">null</span>;
  if (value === undefined) return <span className="text-ink-400">undefined</span>;
  if (typeof value === 'string') return <span className="text-emerald-700">&quot;{value}&quot;</span>;
  if (typeof value === 'number') return <span className="text-accent-700">{value}</span>;
  if (typeof value === 'boolean') return <span className="text-amber-700">{String(value)}</span>;
  return <span>{String(value)}</span>;
}

function JsonNode({
  label, value, depth, query, defaultCollapsedDepth,
}: {
  label: string | null;
  value: JsonValue;
  depth: number;
  query: string;
  defaultCollapsedDepth: number;
}) {
  const isCollapsible = value !== null && typeof value === 'object';
  const [open, setOpen] = useState(depth < defaultCollapsedDepth);

  // While searching, force-expand any branch that contains a match so the
  // user doesn't have to manually open every node to find it.
  const forcedOpen = query !== '' && isCollapsible && valueMatches(value, query);
  const expanded = isCollapsible && (open || forcedOpen);

  if (!isCollapsible) {
    if (query !== '' && !valueMatches(value, query)) return null;
    return (
      <div className="pl-4 py-0.5 font-mono text-[12px] leading-relaxed">
        {label !== null && <span className="text-ink-500">{label}: </span>}
        <Leaf value={value} />
      </div>
    );
  }

  const entries = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as const)
    : Object.entries(value as Record<string, unknown>);
  const visibleEntries = query === '' ? entries : entries.filter(([k, v]) => (
    k.toLowerCase().includes(query.toLowerCase()) || valueMatches(v, query)
  ));
  if (query !== '' && visibleEntries.length === 0 && !label?.toLowerCase().includes(query.toLowerCase())) {
    return null;
  }

  const isArray = Array.isArray(value);
  const summary = isArray ? `Array(${entries.length})` : `Object(${entries.length})`;

  return (
    <div className="py-0.5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 font-mono text-[12px] text-ink-700 hover:text-ink-900"
      >
        <span className="text-ink-400 w-3 inline-block">{expanded ? '▾' : '▸'}</span>
        {label !== null && <span className="text-ink-500">{label}:</span>}
        <span className="text-ink-400">{summary}</span>
      </button>
      {expanded && (
        <div className="pl-3 border-l border-slate-100 ml-1.5">
          {visibleEntries.map(([k, v]) => (
            <JsonNode
              key={k}
              label={isArray ? `[${k}]` : k}
              value={v}
              depth={depth + 1}
              query={query}
              defaultCollapsedDepth={defaultCollapsedDepth}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function JsonTree({
  value,
  defaultCollapsedDepth = 2,
  searchable = true,
}: {
  value: JsonValue;
  defaultCollapsedDepth?: number;
  searchable?: boolean;
}) {
  const [query, setQuery] = useState('');
  const hasAnyMatch = useMemo(
    () => query === '' || valueMatches(value, query),
    [value, query],
  );

  return (
    <div>
      {searchable && (
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search within output…"
          className="mb-2 w-full rounded-md border border-slate-300 px-2 py-1.5 text-xs"
        />
      )}
      {!hasAnyMatch ? (
        <div className="text-xs text-ink-500 py-2">No matches for &quot;{query}&quot;.</div>
      ) : (
        <JsonNode
          label={null}
          value={value}
          depth={0}
          query={query}
          defaultCollapsedDepth={defaultCollapsedDepth}
        />
      )}
    </div>
  );
}
