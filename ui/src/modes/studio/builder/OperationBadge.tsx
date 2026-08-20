import type { MCPOperationClass } from '../../../api/types';

const OPERATION_STYLES: Record<MCPOperationClass, string> = {
  read: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  write: 'border-amber-200 bg-amber-50 text-amber-800',
  destructive: 'border-red-200 bg-red-50 text-red-700',
  unknown: 'border-slate-200 bg-slate-50 text-ink-600',
  external_action: 'border-sky-200 bg-sky-50 text-sky-700',
};

const OPERATION_TITLES: Record<MCPOperationClass, string> = {
  read: 'Reads data. Changes nothing.',
  write: 'Changes data in the connected system.',
  destructive: 'Deletes or irreversibly changes data in the connected system.',
  unknown: 'Unclassified — treated as a write.',
  external_action: 'Triggers an action outside the platform — not a simple read or write.',
};

/** Shared safety-classification badge — one visual language for every node
 *  that touches something outside the platform, not just MCP tools. Used by
 *  MCPToolConfig, EmailConfig, ExternalActionConfig, SQLQueryConfig and
 *  MCPToolPicker, so an author (and, on the canvas/Cockpit, a reader) sees
 *  the same badge regardless of which node type produced it. */
export function OperationBadge({ operation }: { operation: MCPOperationClass }) {
  return (
    <span
      className={`inline-flex rounded-full border px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide ${
        OPERATION_STYLES[operation] ?? OPERATION_STYLES.unknown
      }`}
      title={OPERATION_TITLES[operation] ?? OPERATION_TITLES.unknown}
    >
      {operation}
    </span>
  );
}
