import { useMemo, useState } from 'react';
import type { NodeTypeManifest } from '../../api/types';
import { Icon, type IconName } from '../../components/ui/Icon';
import { ExecutionKindBadge } from './builder/ExecutionKindBadge';
import { NodeTypeAskAi } from './NodeTypeAskAi';

// Fixed display order — otherwise groups would shuffle on every reload
// based on whatever order the registry happens to iterate in.
//
// "Core Building Blocks" leads, and the specialized capabilities are collapsed
// behind a disclosure. That ordering is the product argument made visible: a new
// business workflow should be expressible in the small reusable vocabulary, and
// an author who scrolls past forty domain agents to find it will not believe
// that. Nothing is removed — existing workflows keep every node type they use.
const CATEGORY_ORDER = [
  'Core Building Blocks',
  'Control & Flow',
  'Research & Discovery',
  'Evidence & Retrieval',
  'Proposal Engineering',
  'Multimodal',
  'Document Rendering & Export',
  'Integrations',
  'Other',
];

// Deprecated in favor of TransformAgent (see app/nodes/ai_task.py and
// app/nodes/data_transform.py). Hidden from the palette so authors can't
// start a new node with either; both types stay registered so any
// already-saved instance still opens and configures normally in the
// Inspector.
const HIDDEN_FROM_PALETTE = new Set(['AITaskAgent', 'DataTransformAgent']);

// Business-language names for the core primitives. The registry key is the
// technical contract (and stays visible in the inspector); the palette is where
// a non-technical author decides what to drag, so it reads as the capability
// rather than as a class name.
const PALETTE_LABELS: Record<string, string> = {
  WorkflowInputAgent: 'Input',
  AITaskAgent: 'AI Task',
  DecisionAgent: 'Decision',
  RouterAgent: 'Router',
  DataTransformAgent: 'Transform',
  TransformAgent: 'Transform',
  HumanInLoopAgent: 'Human Review',
  EmailAgent: 'Email',
  MCPToolAgent: 'MCP Tool',
  TextAssemblerAgent: 'Join',
};

function groupByCategory(types: NodeTypeManifest[]): [string, NodeTypeManifest[]][] {
  const groups = new Map<string, NodeTypeManifest[]>();
  for (const t of types) {
    const list = groups.get(t.category) ?? [];
    list.push(t);
    groups.set(t.category, list);
  }
  const known = CATEGORY_ORDER.filter(c => groups.has(c));
  const unknown = [...groups.keys()].filter(c => !CATEGORY_ORDER.includes(c)).sort();
  return [...known, ...unknown].map(c => [c, groups.get(c)!]);
}

function matchesQuery(t: NodeTypeManifest, query: string): boolean {
  if (!query) return true;
  const label = PALETTE_LABELS[t.type_name] ?? '';
  const haystack = `${t.type_name} ${label} ${t.description} ${t.category}`.toLowerCase();
  return haystack.includes(query);
}

function NodeTypeCard({
  t,
  onAdd,
}: {
  t: NodeTypeManifest;
  onAdd: (typeName: string) => void;
}) {
  const [askingAi, setAskingAi] = useState(false);
  return (
    <div
      draggable
      onDragStart={e => {
        // React Flow checks this exact MIME type on drop.
        e.dataTransfer.setData('application/reactflow', t.type_name);
        e.dataTransfer.effectAllowed = 'move';
      }}
      className="group relative rounded-md border border-slate-200 bg-white px-3 py-2 cursor-grab transition hover:border-accent-600 hover:shadow-sm"
      title={t.description}
    >
      <button
        type="button"
        onClick={e => { e.stopPropagation(); setAskingAi(true); }}
        title={`Ask AI about ${t.type_name}`}
        className="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full border border-slate-300 bg-white text-[11px] text-ink-500 opacity-0 hover:border-accent-600 hover:text-accent-700 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        ?
      </button>
      <div className="flex items-start gap-2 pr-5">
        <span className="mt-0.5 flex-none text-ink-400">
          <Icon name={(t.icon as IconName) ?? 'topology'} size={14} />
        </span>
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-sm font-medium text-ink-900">
              {PALETTE_LABELS[t.type_name] ?? t.type_name}
            </span>
            {t.execution_kind && <ExecutionKindBadge compact kind={t.execution_kind} />}
          </div>
          <div className="mt-0.5 line-clamp-2 text-xs text-ink-500">{t.description}</div>
        </div>
      </div>
      <button
        type="button"
        onClick={e => { e.stopPropagation(); onAdd(t.type_name); }}
        aria-label={`Add ${t.type_name} to the canvas`}
        className="mt-2 w-full rounded border border-slate-200 py-1 text-[11px] font-medium text-accent-700 opacity-0 transition hover:bg-accent-50 group-hover:opacity-100 group-focus-within:opacity-100"
      >
        + Add
      </button>
      {askingAi && <NodeTypeAskAi typeName={t.type_name} onClose={() => setAskingAi(false)} />}
    </div>
  );
}

export function NodePalette({
  types,
  onAdd,
  onClose,
}: {
  types: NodeTypeManifest[];
  onAdd: (typeName: string) => void;
  onClose?: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState('');

  const filtered = useMemo(
    () =>
      types
        .filter(t => !HIDDEN_FROM_PALETTE.has(t.type_name))
        .filter(t => matchesQuery(t, query.trim().toLowerCase())),
    [types, query],
  );
  const groups = useMemo(() => groupByCategory(filtered), [filtered]);

  function toggle(category: string) {
    setCollapsed(current => {
      const next = new Set(current);
      if (next.has(category)) next.delete(category);
      else next.add(category);
      return next;
    });
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <div className="text-xs uppercase tracking-wide text-ink-500">Node types</div>
        {onClose && (
          <button
            aria-label="Close node library"
            className="text-ink-500 hover:text-ink-900"
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        )}
      </div>
      <div className="px-3 pt-3">
        <input
          aria-label="Search node types"
          className="builder-field"
          onChange={event => setQuery(event.target.value)}
          placeholder="Search by name, description, category"
          type="search"
          value={query}
        />
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {groups.length === 0 && (
          <div className="rounded-md border border-dashed border-slate-300 p-4 text-center text-xs text-ink-500">
            No node types match &ldquo;{query}&rdquo;.
          </div>
        )}
        {groups.map(([category, items]) => {
          const isCollapsed = collapsed.has(category);
          return (
            <div key={category}>
              <button
                type="button"
                onClick={() => toggle(category)}
                className="flex w-full items-center justify-between px-2 py-1 text-xs font-semibold text-ink-700 hover:text-ink-900"
              >
                <span>{category}</span>
                <span className="text-ink-400">
                  {items.length} {isCollapsed ? '▸' : '▾'}
                </span>
              </button>
              {!isCollapsed && (
                <div className="mt-1 space-y-1">
                  {items.map(t => (
                    <NodeTypeCard key={t.type_name} t={t} onAdd={onAdd} />
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}