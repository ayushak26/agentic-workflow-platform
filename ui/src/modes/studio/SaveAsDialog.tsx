import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import { isValidSlug, slugify } from './workflow-naming';

export function SaveAsDialog({
  initialDisplayName,
  onCancel,
  onConfirm,
}: {
  initialDisplayName: string;
  onCancel: () => void;
  onConfirm: (args: { displayName: string; slug: string }) => Promise<void>;
}) {
  const [displayName, setDisplayName] = useState(initialDisplayName);
  const [slug, setSlug] = useState(() => slugify(initialDisplayName));
  const [slugEdited, setSlugEdited] = useState(false);
  const [existingNames, setExistingNames] = useState<Set<string> | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listWorkflows()
      .then(list => setExistingNames(new Set(list.map(w => w.name))))
      .catch(() => setExistingNames(new Set()));
  }, []);

  function onDisplayNameChange(next: string) {
    setDisplayName(next);
    if (!slugEdited) setSlug(slugify(next));
  }

  const collision = existingNames?.has(slug) ?? false;
  const slugValid = isValidSlug(slug);
  const canSubmit =
    displayName.trim().length > 0 && slugValid && !collision && !saving && existingNames !== null;

  async function submit() {
    if (!canSubmit) return;
    setSaving(true);
    setError(null);
    try {
      await onConfirm({ displayName: displayName.trim(), slug });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-lg font-semibold">Name this workflow</h2>
        </div>
        <div className="px-6 py-4 space-y-4">
          <div>
            <label className="block text-sm font-medium text-ink-900">Display name</label>
            <input
              autoFocus
              value={displayName}
              onChange={e => onDisplayNameChange(e.target.value)}
              className="mt-1 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-ink-900">File name</label>
            <input
              value={slug}
              onChange={e => { setSlug(e.target.value); setSlugEdited(true); }}
              className="mt-1 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border font-mono"
            />
            {!slugValid && (
              <p className="text-xs text-red-600 mt-1">Letters, numbers, underscore, and hyphen only.</p>
            )}
            {slugValid && collision && (
              <p className="text-xs text-red-600 mt-1">
                A workflow named "{slug}" already exists — saving would overwrite it. Choose a different name.
              </p>
            )}
          </div>
          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}
        </div>
        <div className="px-6 py-4 border-t border-slate-200 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={saving}
            className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  );
}
