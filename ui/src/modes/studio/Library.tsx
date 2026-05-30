import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { WorkflowSummary } from '../../api/types';
import { Spinner } from '../../components/Spinner';

export function Library() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.listWorkflows()
      .then(setWorkflows)
      .catch(e => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="p-8 text-bad">
        Failed to load workflows: {error}
      </div>
    );
  }

  if (workflows === null) {
    return (
      <div className="p-8">
        <Spinner label="Loading workflows…" />
      </div>
    );
  }

  const current = workflows.find(w => w.name === selected) ?? null;

  return (
    <div className="h-full flex">
      {/* Left: list */}
      <aside className="w-80 border-r border-slate-200 bg-white overflow-y-auto">
        <div className="p-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="font-medium">Workflows</h2>
          <button
            onClick={() => navigate('/studio/builder')}
            className="text-sm px-3 py-1 rounded-md bg-accent-600 text-white hover:bg-accent-500"
          >
            + New
          </button>
        </div>
        <ul>
          {workflows.length === 0 && (
            <li className="p-4 text-ink-500 text-sm">No workflows yet. Click "+ New" to create one.</li>
          )}
          {workflows.map(w => (
            <li key={w.name}>
              <button
                onClick={() => setSelected(w.name)}
                className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 ${
                  selected === w.name ? 'bg-slate-100' : ''
                }`}
              >
                <div className="font-medium text-ink-900">{w.name}</div>
                <div className="text-xs text-ink-500 mt-1 line-clamp-2">
                  {w.description || <i>No description.</i>}
                </div>
                <div className="text-xs text-ink-500 mt-1">{w.node_count} nodes</div>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      {/* Right: detail */}
      <section className="flex-1 p-8">
        {current === null ? (
          <div className="text-ink-500">Select a workflow to see its summary.</div>
        ) : (
          <div className="max-w-3xl">
            <h2 className="text-2xl font-semibold">{current.name}</h2>
            <p className="mt-3 text-ink-700">{current.description || <i>No description.</i>}</p>
            <div className="mt-2 text-sm text-ink-500">{current.node_count} nodes</div>
            <div className="mt-8 flex gap-3">
              <button
                onClick={() => navigate(`/studio/builder/${current.name}`)}
                className="px-4 py-2 rounded-md bg-accent-600 text-white hover:bg-accent-500"
              >
                Edit in Builder
              </button>
              <button
                onClick={() => alert('Run wired in 9B.3')}
                className="px-4 py-2 rounded-md border border-slate-300 hover:bg-slate-50"
              >
                Run
              </button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}