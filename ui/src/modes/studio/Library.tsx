import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { WorkflowSummary } from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { parseYaml, type WorkflowInputSpec } from './yaml-bridge';
import { RunDialog } from './RunDialog';

export function Library() {
  const navigate = useNavigate();
  const [workflows, setWorkflows] = useState<WorkflowSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{
    workflowName: string;
    workflowYaml: string;
    inputs: Record<string, WorkflowInputSpec>;
  } | null>(null);

  useEffect(() => {
    api.listWorkflows().then(setWorkflows).catch(e => setError(String(e)));
  }, []);

  async function onRun(workflowName: string) {
    setRunError(null);
    try {
      const { yaml: yamlText } = await api.getWorkflow(workflowName);
      const wf = parseYaml(yamlText);
      const inputs = wf.inputs ?? {};

      if (Object.keys(inputs).length === 0) {
        // No inputs — launch directly.
        const runId = crypto.randomUUID();
        navigate(`/cockpit/${runId}`, {
          state: { workflowYaml: yamlText, workflowName, inputs: {} },
        });
      } else {
        // Open the inputs dialog.
        setDialog({ workflowName, workflowYaml: yamlText, inputs });
      }
    } catch (e: any) {
      setRunError(String(e.message ?? e));
    }
  }

  if (error) return <div className="p-8 text-bad">Failed to load workflows: {error}</div>;
  if (workflows === null) return <div className="p-8"><Spinner label="Loading workflows…" /></div>;

  const current = workflows.find(w => w.name === selected) ?? null;

  return (
    <div className="h-full flex">
      <aside className="w-80 border-r border-slate-200 bg-white overflow-y-auto">
        <div className="p-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="font-medium">Workflows</h2>
          <button
            onClick={() => navigate('/builder')}
            className="text-sm px-3 py-1 rounded-md bg-accent-600 text-white hover:bg-accent-500"
          >
            + New
          </button>
        </div>
        <ul>
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
                onClick={() => navigate(`/builder/${current.name}`)}
                className="px-4 py-2 rounded-md bg-accent-600 text-white hover:bg-accent-500"
              >
                Edit in Builder
              </button>
              <button
                onClick={() => onRun(current.name)}
                className="px-4 py-2 rounded-md border border-slate-300 hover:bg-slate-50"
              >
                Run
              </button>
            </div>
            {runError && <div className="mt-3 text-sm text-bad">Run failed to start: {runError}</div>}
          </div>
        )}
      </section>

      {dialog && (
        <RunDialog
          workflowName={dialog.workflowName}
          workflowYaml={dialog.workflowYaml}
          inputs={dialog.inputs}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}