import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import type { WorkflowInputSpec } from './yaml-bridge';

export function RunDialog({
  workflowName,
  workflowYaml,
  inputs,
  onClose,
}: {
  workflowName: string;
  workflowYaml: string;
  inputs: Record<string, WorkflowInputSpec>;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, string>>({});

  const keys = Object.keys(inputs);

  function launch() {
    const runId = crypto.randomUUID();
    navigate(`/studio/cockpit/${runId}`, {
      state: { workflowYaml, workflowName, inputs: values },
    });
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Run {workflowName}</h2>
            <p className="text-xs text-ink-500 mt-0.5">Provide the workflow's inputs to start.</p>
          </div>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-900 text-xl leading-none">×</button>
        </div>

        <div className="px-6 py-5 space-y-4">
          {keys.length === 0 && (
            <div className="text-sm text-ink-500">This workflow declares no inputs.</div>
          )}
          {keys.map(key => {
            const spec = inputs[key];
            const isFile = spec.type === 'file';
            return (
              <div key={key}>
                <label className="block text-sm font-medium text-ink-700">
                  {key}
                  <span className="ml-2 text-xs font-normal text-ink-500">({spec.type})</span>
                </label>
                {spec.description && <p className="text-xs text-ink-500 mb-1">{spec.description}</p>}
                <textarea
                  rows={isFile ? 6 : 3}
                  value={values[key] ?? ''}
                  onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))}
                  placeholder={
                    isFile
                      ? 'Paste document content here (real file upload to MinIO comes later)'
                      : `Enter ${key}…`
                  }
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border"
                />
              </div>
            );
          })}
        </div>

        <div className="px-6 py-4 border-t border-slate-200 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50"
          >
            Cancel
          </button>
          <button
            onClick={launch}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500"
          >
            Run workflow
          </button>
        </div>
      </div>
    </div>
  );
}