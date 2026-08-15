import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../api/client';
import type { GenerateWorkflowResult } from '../../api/types';
import { InfoPopover } from './builder/InfoPopover';
import { parseYaml } from './yaml-bridge';
import { slugify, uniqueSlug } from './workflow-naming';

export function GenerateWorkflowDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const [prompt, setPrompt] = useState('');
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GenerateWorkflowResult | null>(null);
  const [opening, setOpening] = useState(false);

  async function generate() {
    if (!prompt.trim() || generating) return;
    setGenerating(true);
    setError(null);
    setResult(null);
    try {
      const res = await api.generateWorkflow(prompt.trim());
      setResult(res);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setGenerating(false);
    }
  }

  async function openInBuilder() {
    if (!result || opening) return;
    // A generation that failed its own static/execution check is preflight-
    // invalid by definition — the save endpoint would reject it outright, so
    // there's nothing to persist yet. Hand it to Builder unsaved instead,
    // same as before, so the user can still inspect and fix it up.
    if (!result.success) {
      navigate('/builder', { state: { generatedYaml: result.yaml } });
      return;
    }
    setOpening(true);
    setError(null);
    try {
      const workflow = parseYaml(result.yaml);
      const existing = await api.listWorkflows();
      const slug = uniqueSlug(slugify(workflow.name), new Set(existing.map(w => w.name)));
      await api.saveWorkflow(slug, result.yaml);
      navigate(`/builder/${slug}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setOpening(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-xl max-h-[85vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-lg font-semibold flex items-center gap-1.5">
            Generate a workflow from a prompt
            <InfoPopover feature="workflow_generation" />
          </h2>
          <p className="text-xs text-ink-500 mt-0.5">
            Describe what the workflow should do. It's checked with a static
            preflight and then actually run once end-to-end before being
            handed back, with a couple of automatic repair attempts if
            either check fails.
          </p>
        </div>
        <div className="px-6 py-4 space-y-4">
          <textarea
            rows={5}
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            placeholder="e.g. Search recent news for a competitor's pricing changes and draft a short tactical memo."
            disabled={generating}
            className="block w-full rounded-md border-slate-300 text-sm py-2 px-3 border disabled:opacity-50"
          />

          {error && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {error}
            </div>
          )}

          {result && (
            <div
              className={`rounded-md border p-3 text-xs space-y-2 ${
                result.success ? 'border-emerald-300 bg-emerald-50' : 'border-red-300 bg-red-50'
              }`}
            >
              <div className={`font-semibold ${result.success ? 'text-emerald-700' : 'text-red-700'}`}>
                {result.success ? 'Generated and tested successfully' : 'Could not produce a working workflow'}
              </div>
              {result.execution_skipped_reason && (
                <div className="text-ink-700">{result.execution_skipped_reason}</div>
              )}
              {result.execution_result && (
                <div className="text-ink-700">
                  Test run status: <span className="font-mono">{result.execution_result.status}</span>
                  {result.execution_result.error && ` — ${result.execution_result.error}`}
                </div>
              )}
              <ul className="space-y-1 max-h-32 overflow-y-auto">
                {result.attempts.map((a, i) => (
                  <li key={i} className={a.success ? 'text-emerald-700' : 'text-red-700'}>
                    {a.stage === 'static' ? 'Static check' : 'Test run'}: {a.success ? 'passed' : a.detail}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
        <div className="px-6 py-4 border-t border-slate-200 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={generating || opening}
            className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>
          {result ? (
            <button
              onClick={openInBuilder}
              disabled={opening}
              className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
            >
              {opening ? 'Saving…' : 'Open in Builder'}
            </button>
          ) : (
            <button
              onClick={generate}
              disabled={generating || !prompt.trim()}
              className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
            >
              {generating ? 'Generating & testing…' : 'Generate & test'}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
