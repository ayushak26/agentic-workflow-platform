import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../../api/client';
import type { WorkflowFileReference } from '../../api/types';
import type { CockpitNavState } from './cockpit/useCockpitRun';
import { FileInputField } from './FileInputField';
import { FALLBACK_FILE_CAPABILITIES, fileReferencesFrom } from './fileInputUtils';
import { valueForJsonInput, type WorkflowInputSpec } from './yaml-bridge';

// Only set when this Run is launched from the Builder — carries the
// context Cockpit needs to offer "Back to Builder" and to label a node/
// branch test run distinctly from a full run.
export type RunLaunchContext = Omit<
  Pick<CockpitNavState, 'builderReturnPath' | 'selectedNodeId' | 'viewport' | 'testLabel'>,
  'selectedNodeId'
> & { selectedNodeId?: string | null };

export function RunDialog({
  workflowName,
  workflowYaml,
  inputs,
  onClose,
  launchContext,
}: {
  workflowName: string;
  workflowYaml: string;
  inputs: Record<string, WorkflowInputSpec>;
  onClose: () => void;
  launchContext?: RunLaunchContext;
}) {
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, string>>({});
  const [fileValues, setFileValues] = useState<Record<string, File[]>>({});
  const [fileRefValues, setFileRefValues] = useState<
    Record<string, WorkflowFileReference[]>
  >({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [launchStage, setLaunchStage] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState(FALLBACK_FILE_CAPABILITIES);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importMessage, setImportMessage] = useState<string | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  useEffect(() => {
    api.workflowFileCapabilities()
      .then(setCapabilities)
      .catch(() => {
        // The picker remains usable with the same conservative local defaults.
      });
  }, []);

  const keys = Object.keys(inputs);

  function applyImportedJson(raw: string) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      setImportError('That is not valid JSON.');
      setImportMessage(null);
      return;
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setImportError('Expected a JSON object of {inputName: value}, like the "Copy as JSON" output from run history.');
      setImportMessage(null);
      return;
    }

    const record = parsed as Record<string, unknown>;
    const applied: string[] = [];
    const skipped: string[] = [];
    const nextValues = { ...values };
    const nextFileRefs = { ...fileRefValues };

    for (const key of keys) {
      if (!(key in record)) continue;
      const spec = inputs[key];
      const rawValue = record[key];
      if (spec.type === 'file') {
        const refs = fileReferencesFrom(rawValue);
        if (refs) {
          nextFileRefs[key] = refs;
          setFileValues(current => {
            const next = { ...current };
            delete next[key];
            return next;
          });
          applied.push(key);
        } else {
          skipped.push(`${key} (needs an already-uploaded file reference)`);
        }
        continue;
      }
      const effectiveValue = spec.type === 'json'
        ? valueForJsonInput(rawValue)
        : rawValue;
      nextValues[key] = typeof effectiveValue === 'string'
        ? effectiveValue
        : JSON.stringify(effectiveValue, null, 2);
      applied.push(key);
    }

    for (const key of Object.keys(record)) {
      if (!(key in inputs)) skipped.push(`${key} (not an input on this workflow)`);
    }

    setValues(nextValues);
    setFileRefValues(nextFileRefs);
    setErrors(current => {
      const next = { ...current };
      applied.forEach(key => delete next[key]);
      return next;
    });
    setImportError(null);
    setImportMessage(
      applied.length
        ? `Loaded ${applied.length} input${applied.length === 1 ? '' : 's'}: ${applied.join(', ')}.`
          + (skipped.length ? ` Skipped: ${skipped.join(', ')}.` : '')
        : `Nothing matched this workflow's inputs.${skipped.length ? ` Skipped: ${skipped.join(', ')}.` : ''}`,
    );
  }

  function handleImportFilePicked(file: File) {
    file.text()
      .then(applyImportedJson)
      .catch(() => {
        setImportError(`Couldn't read ${file.name}.`);
        setImportMessage(null);
      });
  }

  async function launch() {
    const nextErrors: Record<string, string> = {};
    const runInputs: Record<string, unknown> = {};

    for (const key of keys) {
      const spec = inputs[key];
      if (spec.type === 'file') {
        const selected = fileValues[key] ?? [];
        const loaded = fileRefValues[key] ?? [];
        if (spec.required && selected.length === 0 && loaded.length === 0) {
          nextErrors[key] = 'Add at least one file.';
        }
        continue;
      }

      const value = values[key] ?? '';
      if (spec.required && !value.trim()) {
        nextErrors[key] = 'This input is required.';
        continue;
      }
      if (spec.type === 'json' && value.trim()) {
        try {
          runInputs[key] = JSON.parse(value);
        } catch {
          nextErrors[key] = 'Enter valid JSON.';
        }
      } else if (value || spec.required) {
        runInputs[key] = value;
      }
    }

    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }

    setUploading(true);
    setLaunchStage('Checking workflow structure…');
    setLaunchError(null);
    try {
      const preflight = await api.validateWorkflow(workflowYaml);
      if (!preflight.valid) {
        const errors = preflight.issues
          .filter(issue => issue.severity === 'error')
          .slice(0, 6)
          .map(issue => (
            `${issue.code}${issue.node_id ? ` (${issue.node_id})` : ''}: ${
              issue.message
            }`
          ));
        setLaunchError(
          `Workflow blocked before upload/run. ${errors.join(' · ')}`,
        );
        setUploading(false);
        setLaunchStage(null);
        return;
      }

      setLaunchStage('Uploading files…');
      for (const key of keys) {
        const spec = inputs[key];
        if (spec.type !== 'file') continue;
        const loaded = fileRefValues[key] ?? [];
        if (loaded.length > 0) {
          // Already uploaded — e.g. re-supplied from a previous run's JSON.
          runInputs[key] = spec.multiple ? loaded : loaded[0];
          continue;
        }
        const selected = fileValues[key] ?? [];
        if (selected.length === 0) continue;
        const uploaded = await api.uploadWorkflowFiles(selected);
        runInputs[key] = spec.multiple
          ? uploaded.files
          : uploaded.files[0];
      }

      setLaunchStage('Running full zero-token test…');
      const fullPreflight = await api.validateWorkflow(
        workflowYaml,
        runInputs,
        true,
      );
      if (!fullPreflight.valid) {
        const fullErrors = fullPreflight.issues
          .filter(issue => issue.severity === 'error')
          .slice(0, 8)
          .map(issue => (
            `${issue.code}${issue.node_id ? ` (${issue.node_id})` : ''}: ${
              issue.message
            }${issue.suggestion ? ` ${issue.suggestion}` : ''}`
          ));
        setLaunchError(
          `Zero-token test blocked the run. ${fullErrors.join(' · ')}`,
        );
        setUploading(false);
        setLaunchStage(null);
        return;
      }

      const runId = crypto.randomUUID();
      // A Builder-originated launch (a "Run in Cockpit" or a node/branch
      // test) always carries launchContext and stays on the technical
      // Cockpit surface. A normal Library launch has none and defaults to
      // the business-language Business View surface instead.
      const surface = launchContext ? 'cockpit' : 'business';
      navigate(`/${surface}/${runId}`, {
        state: { workflowYaml, workflowName, inputs: runInputs, ...launchContext },
      });
    } catch (error: unknown) {
      setLaunchError(error instanceof Error ? error.message : String(error));
      setUploading(false);
      setLaunchStage(null);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200 flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold">Run {workflowName}</h2>
            <p className="text-xs text-ink-500 mt-0.5">
              Add files and provide the workflow&apos;s other inputs.
            </p>
          </div>
          <button
            onClick={onClose}
            disabled={uploading}
            className="text-ink-500 hover:text-ink-900 text-xl leading-none disabled:opacity-50"
          >
            ×
          </button>
        </div>

        <div className="px-6 py-5 space-y-5">
          {keys.length === 0 && (
            <div className="text-sm text-ink-500">
              This workflow declares no inputs.
            </div>
          )}

          {keys.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <button
                type="button"
                onClick={() => setImportOpen(open => !open)}
                className="text-xs font-medium text-accent-700 hover:underline"
              >
                {importOpen ? 'Hide' : 'Import inputs from JSON'}
              </button>
              {!importOpen && (
                <p className="mt-1 text-xs text-ink-500">
                  Paste or upload JSON from Run History &mdash; &quot;Copy as
                  JSON&quot; (this run&apos;s inputs) or &quot;Copy run as
                  workflow inputs&quot; (every node&apos;s output, keyed by
                  node id) &mdash; to refill this form. Only keys matching this
                  workflow&apos;s declared inputs are used; everything else is
                  skipped and reported below.
                </p>
              )}
              {importOpen && (
                <div className="mt-2 space-y-2">
                  <textarea
                    rows={5}
                    value={importText}
                    onChange={event => setImportText(event.target.value)}
                    placeholder='{"inputName": "value", ...}'
                    className="block w-full rounded-md border-slate-300 text-xs py-2 px-3 border font-mono"
                  />
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => applyImportedJson(importText)}
                      disabled={!importText.trim()}
                      className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs hover:bg-accent-500 disabled:opacity-50"
                    >
                      Apply pasted JSON
                    </button>
                    <label className="px-3 py-1.5 rounded-md border border-slate-300 text-xs hover:bg-slate-100 cursor-pointer">
                      Upload JSON file
                      <input
                        type="file"
                        accept=".json,application/json"
                        className="sr-only"
                        onChange={event => {
                          const file = event.target.files?.[0];
                          if (file) handleImportFilePicked(file);
                          event.target.value = '';
                        }}
                      />
                    </label>
                  </div>
                  {importMessage && (
                    <p className="text-xs text-ink-700">{importMessage}</p>
                  )}
                  {importError && (
                    <p className="text-xs text-bad">{importError}</p>
                  )}
                </div>
              )}
            </div>
          )}

          {keys.map((key, index) => {
            const spec = inputs[key];
            if (spec.type === 'file') {
              return (
                <FileInputField
                  key={key}
                  inputId={`workflow-file-${index}`}
                  inputName={key}
                  spec={spec}
                  files={fileValues[key] ?? []}
                  loadedRefs={fileRefValues[key] ?? []}
                  onClearLoaded={() => {
                    setFileRefValues(current => {
                      const next = { ...current };
                      delete next[key];
                      return next;
                    });
                  }}
                  capabilities={capabilities}
                  error={errors[key]}
                  onChange={(nextFiles, nextError) => {
                    setFileValues(current => ({
                      ...current,
                      [key]: nextFiles,
                    }));
                    setErrors(current => {
                      const next = { ...current };
                      if (nextError) next[key] = nextError;
                      else delete next[key];
                      return next;
                    });
                  }}
                />
              );
            }

            return (
              <div key={key}>
                <label className="block text-sm font-medium text-ink-700">
                  {key}
                  {spec.required && <span className="ml-1 text-bad">*</span>}
                  <span className="ml-2 text-xs font-normal text-ink-500">
                    ({spec.type})
                  </span>
                </label>
                {spec.description && (
                  <p className="text-xs text-ink-500 mb-1">{spec.description}</p>
                )}
                <textarea
                  rows={spec.type === 'json' ? 6 : 3}
                  value={values[key] ?? ''}
                  onChange={event => {
                    setValues(current => ({
                      ...current,
                      [key]: event.target.value,
                    }));
                    setErrors(current => {
                      const next = { ...current };
                      delete next[key];
                      return next;
                    });
                  }}
                  placeholder={
                    spec.type === 'json'
                      ? '{"key": "value"}'
                      : `Enter ${key}…`
                  }
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border font-mono"
                />
                {errors[key] && (
                  <p className="mt-1 text-xs text-bad">{errors[key]}</p>
                )}
              </div>
            );
          })}
          {launchError && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-bad">
              {launchError}
            </div>
          )}
        </div>

        <div className="px-6 py-4 border-t border-slate-200 flex items-center justify-between gap-3">
          <span className="text-xs text-ink-500">
            Files are stored once, then services, model access, and inputs are
            tested with 0 generation tokens before the run opens.
          </span>
          <div className="flex gap-3">
            <button
              onClick={onClose}
              disabled={uploading}
              className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={launch}
              disabled={uploading}
              className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
            >
              {uploading ? (launchStage ?? 'Checking…') : 'Test & run workflow'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
