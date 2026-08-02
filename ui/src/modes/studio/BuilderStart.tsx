import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { WorkflowSummary } from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { Icon } from '../../components/ui/Icon';

const NAME_PATTERN = /^[A-Za-z0-9_-]+$/;

export function BuilderStart({
  onBlank,
  onTemplate,
}: {
  onBlank: (name: string) => void;
  onTemplate: (name: string, templateName: string) => void;
}) {
  const [name, setName] = useState('');
  const [templates, setTemplates] = useState<WorkflowSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listWorkflows()
      .then(setTemplates)
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);

  const trimmed = name.trim();
  const valid = trimmed.length > 0 && NAME_PATTERN.test(trimmed);

  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col justify-center px-6 py-10">
      <div className="text-center">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-accent-50 text-accent-700">
          <Icon name="topology" size={22} />
        </div>
        <h1 className="mt-4 text-xl font-semibold text-ink-950">Create a workflow</h1>
        <p className="mt-1 text-sm text-ink-500">
          Name your workflow, then start from a blank canvas or an existing one.
        </p>
      </div>

      <div className="mx-auto mt-6 w-full max-w-sm">
        <label className="block text-xs font-medium text-ink-700" htmlFor="new-workflow-name">
          Workflow file name
        </label>
        <input
          autoFocus
          id="new-workflow-name"
          className="mt-1 block w-full rounded-md border border-slate-300 px-3 py-2 text-sm font-mono"
          onChange={event => setName(event.target.value)}
          placeholder="my_new_workflow"
          value={name}
        />
        {trimmed.length > 0 && !valid && (
          <p className="mt-1 text-xs text-bad">Letters, numbers, underscore, and hyphen only.</p>
        )}
      </div>

      <div className="mx-auto mt-4">
        <button
          className="ui-button ui-button--primary"
          disabled={!valid}
          onClick={() => onBlank(trimmed)}
          type="button"
        >
          Start from a blank canvas
        </button>
      </div>

      {error && (
        <div className="mx-auto mt-6 max-w-lg rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      <div className="mt-8">
        <div className="text-center text-xs font-semibold uppercase tracking-wide text-ink-500">
          Or start from an existing workflow
        </div>
        <div className="mt-3 max-h-64 overflow-y-auto rounded-lg border border-slate-200">
          {templates === null ? (
            <div className="p-4"><Spinner label="Loading workflows…" /></div>
          ) : templates.length === 0 ? (
            <div className="p-4 text-center text-xs text-ink-500">No saved workflows yet.</div>
          ) : (
            <ul className="divide-y divide-slate-100">
              {templates.map(template => (
                <li key={template.name}>
                  <button
                    className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                    disabled={!valid}
                    onClick={() => onTemplate(trimmed, template.name)}
                    type="button"
                  >
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium text-ink-900">{template.name}</span>
                      <span className="block truncate text-xs text-ink-500">
                        {template.description || 'No description'}
                      </span>
                    </span>
                    <span className="flex-none text-xs text-ink-400">{template.node_count} nodes</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}
