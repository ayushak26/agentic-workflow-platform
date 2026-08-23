import { useEffect, useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type { PrivateChatWorkflowSummary, WorkflowSummary } from '../../../api/types';
import { isValidSlug, slugify } from '../workflow-naming';

type Tab = 'generate' | 'import' | 'existing';
type OutputPreference = 'auto' | 'text' | 'code' | 'image' | 'pdf' | 'docx' | 'pptx' | 'xlsx';

export function AddWorkflowDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (workflow: PrivateChatWorkflowSummary) => void;
}) {
  const [tab, setTab] = useState<Tab>('generate');
  const [displayName, setDisplayName] = useState('');
  const [slug, setSlug] = useState('');
  const [slugEdited, setSlugEdited] = useState(false);
  const [prompt, setPrompt] = useState('');
  const [yamlText, setYamlText] = useState('');
  const [output, setOutput] = useState<OutputPreference>('auto');
  const [existing, setExisting] = useState<WorkflowSummary[]>([]);
  const [selectedExisting, setSelectedExisting] = useState('');
  const [query, setQuery] = useState('');
  const [loadingExisting, setLoadingExisting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (tab !== 'existing' || existing.length > 0 || loadingExisting) return;
    setLoadingExisting(true);
    api.listWorkflows()
      .then(setExisting)
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoadingExisting(false));
  }, [existing.length, loadingExisting, tab]);

  const visibleExisting = useMemo(() => existing.filter(item => (
    `${item.name} ${item.description} ${item.library?.title ?? ''}`
      .toLowerCase().includes(query.trim().toLowerCase())
  )), [existing, query]);

  function updateName(value: string) {
    setDisplayName(value);
    if (!slugEdited) setSlug(slugify(value));
  }

  const valid = isValidSlug(slug)
    && displayName.trim().length > 0
    && (tab === 'generate'
      ? prompt.trim().length > 0
      : tab === 'import'
        ? yamlText.trim().length > 0
        : selectedExisting.length > 0);

  async function submit() {
    if (!valid || saving) return;
    setSaving(true);
    setError(null);
    try {
      const created = tab === 'generate'
        ? await api.generatePrivateChatWorkflow({
            prompt: prompt.trim(), slug, display_name: displayName.trim(),
            preferred_output_type: output,
          })
        : tab === 'import'
          ? await api.importPrivateChatWorkflow({
              slug, display_name: displayName.trim(), yaml: yamlText,
            })
          : await api.copyPrivateChatWorkflow({
              workflow_name: selectedExisting, slug, display_name: displayName.trim(),
            });
      onCreated(created);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-labelledby="add-workflow-title">
      <div className="w-full max-w-2xl rounded-xl bg-white shadow-xl">
        <div className="flex items-start justify-between border-b border-slate-200 px-5 py-4">
          <div>
            <h2 id="add-workflow-title" className="text-lg font-semibold text-ink-900">Add workflow</h2>
            <p className="mt-0.5 text-xs text-ink-500">New workflows are private to you until you request publication.</p>
          </div>
          <button type="button" aria-label="Close" onClick={onClose} className="text-xl text-ink-400">×</button>
        </div>
        <div className="p-5">
          <div className="flex gap-1 rounded-lg bg-slate-100 p-1">
            {([
              ['generate', 'Create with AI'],
              ['import', 'Import YAML'],
              ['existing', 'Add existing'],
            ] as const).map(([value, label]) => (
              <button key={value} type="button" onClick={() => { setTab(value); setError(null); }} className={`flex-1 rounded-md px-3 py-2 text-xs font-medium ${tab === value ? 'bg-white text-ink-900 shadow-sm' : 'text-ink-500'}`}>
                {label}
              </button>
            ))}
          </div>

          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <label className="text-xs font-medium text-ink-700">
              Display name
              <input value={displayName} onChange={event => updateName(event.target.value)} placeholder="Customer research" className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
            </label>
            <label className="text-xs font-medium text-ink-700">
              Private slug
              <input value={slug} onChange={event => { setSlugEdited(true); setSlug(event.target.value); }} placeholder="customer_research" className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
              {slug && !isValidSlug(slug) && <span className="mt-1 block text-[11px] text-bad">Use letters, numbers, underscores, or hyphens.</span>}
            </label>
          </div>

          {tab === 'generate' && (
            <div className="mt-4 space-y-3">
              <label className="block text-xs font-medium text-ink-700">
                What should this workflow do?
                <textarea rows={6} value={prompt} onChange={event => setPrompt(event.target.value)} placeholder="Research a company using trusted web sources and produce an executive brief…" className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
              </label>
              <label className="block text-xs font-medium text-ink-700">
                Preferred visible output
                <select value={output} onChange={event => setOutput(event.target.value as OutputPreference)} className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
                  {['auto', 'text', 'code', 'image', 'pdf', 'docx', 'pptx', 'xlsx'].map(value => <option key={value} value={value}>{value === 'auto' ? 'Auto' : value.toUpperCase()}</option>)}
                </select>
              </label>
            </div>
          )}

          {tab === 'import' && (
            <label className="mt-4 block text-xs font-medium text-ink-700">
              Workflow YAML
              <textarea rows={12} value={yamlText} onChange={event => setYamlText(event.target.value)} placeholder="Paste the complete workflow YAML…" className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-xs" />
            </label>
          )}

          {tab === 'existing' && (
            <div className="mt-4">
              <input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Search existing workflows…" className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
              <div className="mt-2 max-h-60 overflow-y-auto rounded-md border border-slate-200">
                {loadingExisting && <p className="p-3 text-xs text-ink-500">Loading workflows…</p>}
                {visibleExisting.map(item => (
                  <label key={item.name} className="flex cursor-pointer gap-2 border-b border-slate-100 p-3 last:border-0 hover:bg-slate-50">
                    <input type="radio" name="existing-workflow" value={item.name} checked={selectedExisting === item.name} onChange={() => setSelectedExisting(item.name)} />
                    <span><span className="block text-sm font-medium text-ink-800">{item.library?.title ?? item.name}</span><span className="block text-xs text-ink-500">{item.description}</span></span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {error && <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}
          <div className="mt-5 flex justify-end gap-2">
            <button type="button" onClick={onClose} className="rounded-md border border-slate-300 px-4 py-2 text-sm text-ink-700">Cancel</button>
            <button type="button" disabled={!valid || saving} onClick={() => void submit()} className="rounded-md bg-accent-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">
              {saving ? (tab === 'generate' ? 'Generating and validating…' : 'Validating and adding…') : 'Add privately'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}