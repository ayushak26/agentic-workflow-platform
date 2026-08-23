import { useEffect, useState } from 'react';
import { api } from '../../api/client';
import type { WorkflowVersionSummary } from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { parseYaml } from './yaml-bridge';

function formatTimestamp(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function summarize(yaml: string): { nodes: number; edges: number } | null {
  try {
    const parsed = parseYaml(yaml);
    return { nodes: parsed.nodes?.length ?? 0, edges: parsed.edges?.length ?? 0 };
  } catch {
    return null;
  }
}

export function VersionHistoryPanel({
  workflowName,
  currentYaml,
  onClose,
  onRestored,
}: {
  workflowName: string;
  currentYaml: string;
  onClose: () => void;
  onRestored: (yaml: string) => void;
}) {
  const [versions, setVersions] = useState<WorkflowVersionSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [previewYaml, setPreviewYaml] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    api.listWorkflowVersions(workflowName)
      .then(setVersions)
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)));
  }, [workflowName]);

  useEffect(() => {
    // Reset the preview whenever the selected version changes (including to
    // none) before fetching the newly selected one.
     
    if (!selectedId) { setPreviewYaml(null); return; }
    setPreviewYaml(null);
    setPreviewError(null);
    api.getWorkflowVersion(workflowName, selectedId)
      .then(result => setPreviewYaml(result.yaml))
      .catch(reason => setPreviewError(reason instanceof Error ? reason.message : String(reason)));
  }, [selectedId, workflowName]);

  const currentSummary = summarize(currentYaml);
  const previewSummary = previewYaml ? summarize(previewYaml) : null;

  async function restore() {
    if (!selectedId) return;
    setRestoring(true);
    setError(null);
    try {
      const result = await api.restoreWorkflowVersion(workflowName, selectedId);
      onRestored(result.yaml);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRestoring(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div className="flex max-h-[80vh] w-full max-w-3xl flex-col overflow-hidden rounded-lg bg-white shadow-xl">
        <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
          <div>
            <h2 className="text-lg font-semibold text-ink-900">Version history</h2>
            <p className="text-xs text-ink-500">
              Every manual save creates an immutable version. Restoring one preflights it first.
            </p>
          </div>
          <button
            aria-label="Close version history"
            className="text-lg leading-none text-ink-500 hover:text-ink-900"
            onClick={onClose}
            type="button"
          >
            ×
          </button>
        </div>

        {error && (
          <div className="mx-6 mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
            {error}
          </div>
        )}

        <div className="flex min-h-0 flex-1 flex-col gap-0 overflow-hidden sm:flex-row">
          <div className="min-h-0 w-full flex-none overflow-y-auto border-b border-slate-200 sm:w-64 sm:border-b-0 sm:border-r">
            {versions === null ? (
              <div className="p-4"><Spinner label="Loading versions…" /></div>
            ) : versions.length === 0 ? (
              <div className="p-4 text-center text-xs text-ink-500">
                No saved versions yet. Save the workflow to create the first one.
              </div>
            ) : (
              <ul>
                {versions.map(version => (
                  <li key={version.version_id}>
                    <button
                      className={`block w-full border-b border-slate-100 px-4 py-3 text-left text-xs hover:bg-slate-50 ${
                        selectedId === version.version_id ? 'bg-accent-50' : ''
                      }`}
                      onClick={() => setSelectedId(version.version_id)}
                      type="button"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-medium text-ink-900">
                          {formatTimestamp(version.created_at)}
                        </span>
                        {version.current && (
                          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                            Current
                          </span>
                        )}
                      </div>
                      <div className="mt-1 text-ink-500">
                        {version.node_count} nodes · workflow v{version.workflow_version}
                      </div>
                      {version.description && (
                        <div className="mt-1 truncate text-ink-500">{version.description}</div>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {!selectedId && (
              <div className="text-xs text-ink-500">Select a version to preview it.</div>
            )}
            {selectedId && previewError && (
              <div className="text-xs text-bad">{previewError}</div>
            )}
            {selectedId && !previewError && previewYaml === null && (
              <Spinner label="Loading version…" />
            )}
            {selectedId && previewYaml !== null && (
              <>
                {currentSummary && previewSummary && (
                  <div className="mb-3 rounded-md border border-ink-100 bg-brand-softer p-3 text-xs text-ink-700">
                    Compared to current: {previewSummary.nodes} vs {currentSummary.nodes} nodes,{' '}
                    {previewSummary.edges} vs {currentSummary.edges} edges.
                  </div>
                )}
                <pre className="max-h-[42vh] overflow-auto rounded-md border border-slate-200 bg-slate-50 p-3 text-[11px] leading-5 text-ink-800">
                  {previewYaml}
                </pre>
              </>
            )}
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-200 px-6 py-4">
          <button className="ui-button ui-button--secondary" onClick={onClose} type="button">
            Close
          </button>
          <button
            className="ui-button ui-button--primary"
            disabled={!selectedId || restoring}
            onClick={() => void restore()}
            type="button"
          >
            {restoring ? 'Restoring…' : 'Restore this version'}
          </button>
        </div>
      </div>
    </div>
  );
}
