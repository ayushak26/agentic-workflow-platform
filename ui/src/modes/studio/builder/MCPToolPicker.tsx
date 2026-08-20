import { useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../../../api/client';
import type { MCPServerInfo, MCPToolInfo } from '../../../api/types';
import { OperationBadge } from './OperationBadge';

/**
 * Add an MCP tool by searching what it does, across every connected system at
 * once — instead of picking a server first and only then discovering what it
 * can do. Reused for two entry points: adding a fresh MCP Tool node from the
 * palette, and "+ Add Next Tool" from an existing one (which passes
 * `rankServerId` so tools on the same system the author is already using sort
 * first — the common case of chaining several lookups on one CRM/ERP).
 *
 * This never creates a node itself — it only resolves which (server, tool)
 * pair the author picked. The caller decides what a selection means:
 * `NodePalette` creates a fresh node, `MCPToolConfig`'s "Add Next Tool" also
 * connects it to the node this picker was opened from.
 */

type Catalog = Array<{ server: MCPServerInfo; tool: MCPToolInfo }>;

function requiredArgumentNames(schema: Record<string, unknown>): string[] {
  const required = schema.required;
  return Array.isArray(required) ? required.filter((name): name is string => typeof name === 'string') : [];
}

function matchesQuery(entry: Catalog[number], query: string): boolean {
  if (!query) return true;
  const haystack = [
    entry.tool.name,
    entry.tool.title,
    entry.tool.description,
    entry.tool.system,
    ...entry.tool.typical_uses,
  ].join(' ').toLowerCase();
  return haystack.includes(query);
}

export function MCPToolPicker({
  onClose,
  onSelect,
  rankServerId,
  title = 'Add an MCP tool',
}: {
  onClose: () => void;
  onSelect: (serverId: string, tool: MCPToolInfo) => void;
  /** Tools on this server sort first — the system the author is already
   *  working with, for "Add Next Tool". */
  rankServerId?: string;
  title?: string;
}) {
  const [servers, setServers] = useState<MCPServerInfo[]>([]);
  const [toolsByServer, setToolsByServer] = useState<Record<string, MCPToolInfo[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    api.mcpServers()
      .then(async result => {
        if (cancelled) return;
        setServers(result.servers);
        const perServer = await Promise.all(
          result.servers.map(server =>
            api.mcpTools(server.id)
              .then(r => [server.id, r.tools] as const)
              .catch(() => [server.id, []] as const),
          ),
        );
        if (cancelled) return;
        setToolsByServer(Object.fromEntries(perServer));
      })
      .catch(err => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const catalog = useMemo<Catalog>(() => {
    const entries: Catalog = [];
    for (const server of servers) {
      for (const tool of toolsByServer[server.id] ?? []) {
        entries.push({ server, tool });
      }
    }
    // Same-server-first (when ranking against a current node), then read
    // before write (the safer, more common next step), then alphabetical.
    return entries.sort((a, b) => {
      if (rankServerId) {
        const aRank = a.server.id === rankServerId ? 0 : 1;
        const bRank = b.server.id === rankServerId ? 0 : 1;
        if (aRank !== bRank) return aRank - bRank;
      }
      if (a.server.id !== b.server.id) return a.server.display_name.localeCompare(b.server.display_name);
      const aWrite = a.tool.operation === 'read' ? 0 : 1;
      const bWrite = b.tool.operation === 'read' ? 0 : 1;
      if (aWrite !== bWrite) return aWrite - bWrite;
      return a.tool.title.localeCompare(b.tool.title);
    });
  }, [servers, toolsByServer, rankServerId]);

  const normalised = query.trim().toLowerCase();
  const filtered = useMemo(
    () => catalog.filter(entry => matchesQuery(entry, normalised)),
    [catalog, normalised],
  );

  // Group the (already-sorted) filtered list by server for display, without
  // losing the sort order above (a plain Map preserves insertion order).
  const grouped = useMemo(() => {
    const groups = new Map<string, { server: MCPServerInfo; entries: Catalog }>();
    for (const entry of filtered) {
      const group = groups.get(entry.server.id);
      if (group) group.entries.push(entry);
      else groups.set(entry.server.id, { server: entry.server, entries: [entry] });
    }
    return [...groups.values()];
  }, [filtered]);

  return (
    <div className="builder-search-backdrop" onMouseDown={onClose} role="presentation">
      <div
        aria-label={title}
        aria-modal="true"
        className="builder-search-panel"
        onMouseDown={event => event.stopPropagation()}
        role="dialog"
      >
        <input
          className="builder-search-input"
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => {
            if (event.key === 'Escape') {
              event.preventDefault();
              onClose();
            }
          }}
          placeholder="Search tools — customer, order, inventory, case…"
          ref={inputRef}
          value={query}
        />

        <div className="builder-search-results">
          {loading && (
            <div className="p-3 text-center text-[11px] text-ink-500">Discovering tools…</div>
          )}
          {error && (
            <div className="m-2 rounded-md border border-red-200 bg-red-50 p-2 text-[11px] text-red-800">
              Could not reach the MCP servers: {error}
            </div>
          )}
          {!loading && !error && grouped.length === 0 && (
            <div className="p-3 text-center text-[11px] text-ink-500">
              {query ? `No tool matches "${query}".` : 'No MCP servers are configured in this deployment.'}
            </div>
          )}
          {grouped.map(({ server, entries }) => (
            <div key={server.id}>
              <div className="sticky top-0 flex items-center gap-1.5 bg-white px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-ink-500">
                {server.display_name}
                {server.is_mock && (
                  <span className="rounded-full border border-sky-200 bg-sky-50 px-1.5 py-0.5 text-[9px] font-semibold text-sky-700">
                    demo data
                  </span>
                )}
              </div>
              {entries.map(({ tool }) => {
                const required = requiredArgumentNames(tool.input_schema);
                return (
                  <button
                    className="flex w-full flex-col items-start gap-0.5 rounded-md px-2.5 py-1.5 text-left hover:bg-accent-50"
                    key={`${server.id}:${tool.name}`}
                    onClick={() => { onSelect(server.id, tool); onClose(); }}
                    type="button"
                  >
                    <div className="flex w-full items-center gap-1.5">
                      <span className="min-w-0 flex-1 truncate font-medium text-ink-900">
                        {tool.title}
                      </span>
                      <OperationBadge operation={tool.operation} />
                    </div>
                    <div className="line-clamp-1 w-full text-left text-[10px] text-ink-500">
                      {tool.description}
                    </div>
                    {required.length > 0 && (
                      <div className="w-full text-left text-[9px] text-ink-400">
                        Needs: {required.join(', ')}
                      </div>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </div>
        <div className="builder-search-hint">Esc to close</div>
      </div>
    </div>
  );
}
