import { useState } from 'react';

export function ErrorNotice({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm text-rose-800">{error}</div>;
}

export function ResourceId({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  if (!value) return <span className="text-xs text-ink-400">—</span>;
  function copy() {
    void navigator.clipboard?.writeText(value).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    });
  }
  return (
    <button
      type="button"
      onClick={copy}
      title="Copy ID"
      className="inline-flex max-w-full items-center gap-1.5 rounded border border-slate-200 bg-slate-50 px-2 py-1 font-mono text-[11px] text-ink-700 hover:bg-slate-100"
    >
      <span className="truncate">{value}</span>
      <span className="text-ink-400">{copied ? '✓' : '⧉'}</span>
    </button>
  );
}

const STATUS_STYLES: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-600',
  building: 'bg-amber-100 text-amber-700',
  queued: 'bg-slate-100 text-slate-600',
  uploading: 'bg-amber-100 text-amber-700',
  parsing: 'bg-amber-100 text-amber-700',
  chunking: 'bg-amber-100 text-amber-700',
  enriching: 'bg-amber-100 text-amber-700',
  embedding: 'bg-amber-100 text-amber-700',
  indexing: 'bg-amber-100 text-amber-700',
  ready: 'bg-emerald-100 text-emerald-700',
  active: 'bg-emerald-100 text-emerald-700',
  completed: 'bg-emerald-100 text-emerald-700',
  partially_completed: 'bg-amber-100 text-amber-700',
  inactive: 'bg-slate-100 text-slate-500',
  failed: 'bg-rose-100 text-rose-700',
  cancelled: 'bg-slate-100 text-slate-500',
  archived: 'bg-slate-100 text-slate-400',
};

export function Status({ value }: { value: string }) {
  const style = STATUS_STYLES[value] ?? 'bg-slate-100 text-slate-600';
  return (
    <span className={`rounded-full px-2.5 py-1 text-[11px] font-medium ${style}`}>
      {value.replaceAll('_', ' ')}
    </span>
  );
}
