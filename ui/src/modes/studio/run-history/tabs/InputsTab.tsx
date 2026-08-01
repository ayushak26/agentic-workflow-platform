import { useState } from 'react';
import type { RunDetail } from '../../../../api/types';
import { CopyButton } from '../../../../components/CopyButton';
import { FileInputValue } from '../../cockpit/node-render';

function isEmptyValue(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') return Object.keys(value).length === 0;
  return false;
}

function downloadJson(value: unknown, filename: string) {
  const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function InputsTab({ run }: { run: RunDetail }) {
  const entries = Object.entries(run.inputs ?? {});
  const [collapsedAll, setCollapsedAll] = useState(false);
  const [openFields, setOpenFields] = useState<Set<string>>(() => new Set(entries.map(([k]) => k)));

  if (entries.length === 0) {
    return <div className="p-6 text-sm text-ink-500">This run recorded no inputs.</div>;
  }

  function toggleField(key: string) {
    setOpenFields((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }

  function expandAll() {
    setOpenFields(new Set(entries.map(([k]) => k)));
    setCollapsedAll(false);
  }
  function collapseAll() {
    setOpenFields(new Set());
    setCollapsedAll(true);
  }

  return (
    <div className="p-4">
      <div className="flex items-center gap-2 mb-3">
        <CopyButton text={JSON.stringify(run.inputs, null, 2)} label="Copy all as JSON" />
        <button
          type="button"
          onClick={() => downloadJson(run.inputs, `${run.run_id}-inputs.json`)}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
        >
          Download input manifest
        </button>
        <button
          type="button"
          onClick={expandAll}
          className="ml-auto rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
        >
          Expand all
        </button>
        <button
          type="button"
          onClick={collapseAll}
          className="rounded border border-slate-300 bg-white px-2 py-1 text-[10px] text-ink-700 hover:bg-slate-100"
        >
          Collapse all
        </button>
      </div>

      <div className="border border-slate-200 rounded-lg divide-y divide-slate-100">
        {entries.map(([key, value]) => {
          const empty = isEmptyValue(value);
          const open = !collapsedAll && openFields.has(key) && !empty;
          return (
            <div key={key}>
              <button
                type="button"
                onClick={() => !empty && toggleField(key)}
                className="w-full flex items-center justify-between px-3.5 py-2.5 text-left hover:bg-slate-50"
              >
                <span className="text-xs font-medium text-ink-700">{key}</span>
                {empty ? (
                  <span className="text-[11px] text-ink-400">Not provided</span>
                ) : (
                  <span className="text-[11px] text-ink-400">{open ? 'Hide' : 'Show'}</span>
                )}
              </button>
              {open && (
                <div className="px-3.5 pb-3">
                  <FileInputValue value={value} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
