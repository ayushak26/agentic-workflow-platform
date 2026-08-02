import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import yaml from 'js-yaml';

import { api } from '../../api/client';
import type {
  PipelineRunDetail,
  PipelineRunSummary,
  PipelineStageStatus,
  PipelineSummary,
  WorkflowFileReference,
} from '../../api/types';
import { Spinner } from '../../components/Spinner';
import { CopyButton } from '../../components/CopyButton';
import { FileInputField } from './FileInputField';
import { FALLBACK_FILE_CAPABILITIES, fileReferencesFrom } from './fileInputUtils';
import { valueForJsonInput, type WorkflowInputSpec } from './yaml-bridge';

type YamlPipeline = {
  name: string;
  description?: string;
  stages: Array<{ id: string; workflow: string; description?: string }>;
  inputs?: Record<string, WorkflowInputSpec>;
};

function parsePipelineYaml(text: string): YamlPipeline {
  return yaml.load(text) as YamlPipeline;
}

const STAGE_STATUS_LABEL: Record<PipelineStageStatus, string> = {
  pending: 'Not started',
  running: 'Running',
  paused: 'Paused (needs review)',
  completed: 'Completed',
  failed: 'Failed',
  rejected: 'Rejected',
};
const STAGE_STATUS_DOT: Record<PipelineStageStatus, string> = {
  pending: 'bg-slate-300',
  running: 'bg-blue-500 animate-pulse',
  paused: 'bg-amber-500',
  completed: 'bg-emerald-500',
  failed: 'bg-red-500',
  rejected: 'bg-amber-500',
};

// ---- Launch dialog ----------------------------------------------------------
function PipelineLaunchDialog({
  pipelineName,
  pipelineYaml,
  inputs,
  onClose,
}: {
  pipelineName: string;
  pipelineYaml: string;
  inputs: Record<string, WorkflowInputSpec>;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [values, setValues] = useState<Record<string, string>>({});
  const [fileValues, setFileValues] = useState<Record<string, File[]>>({});
  const [fileRefValues, setFileRefValues] = useState<
    Record<string, WorkflowFileReference[]>
  >({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [launching, setLaunching] = useState(false);
  const [launchStage, setLaunchStage] = useState<string | null>(null);
  const [launchError, setLaunchError] = useState<string | null>(null);
  const [capabilities, setCapabilities] = useState(FALLBACK_FILE_CAPABILITIES);
  const [importOpen, setImportOpen] = useState(false);
  const [importText, setImportText] = useState('');
  const [importMessage, setImportMessage] = useState<string | null>(null);

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
      setImportMessage('That is not valid JSON.');
      return;
    }
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      setImportMessage('Expected a JSON object of {inputName: value}.');
      return;
    }
    const record = parsed as Record<string, unknown>;
    const applied: string[] = [];
    const skipped: string[] = [];
    const next = { ...values };
    const nextFileRefs = { ...fileRefValues };
    for (const key of keys) {
      if (!(key in record)) continue;
      const spec = inputs[key];
      if (spec.type === 'file') {
        const refs = fileReferencesFrom(record[key]);
        if (refs) {
          nextFileRefs[key] = refs;
          setFileValues(current => {
            const nextFiles = { ...current };
            delete nextFiles[key];
            return nextFiles;
          });
          applied.push(key);
        } else {
          skipped.push(`${key} (needs an already-uploaded file reference)`);
        }
        continue;
      }
      const effective = spec.type === 'json' ? valueForJsonInput(record[key]) : record[key];
      next[key] = typeof effective === 'string' ? effective : JSON.stringify(effective, null, 2);
      applied.push(key);
    }
    for (const key of Object.keys(record)) {
      if (!(key in inputs)) skipped.push(key);
    }
    setValues(next);
    setFileRefValues(nextFileRefs);
    setImportMessage(
      applied.length
        ? `Loaded ${applied.length} input(s): ${applied.join(', ')}.`
          + (skipped.length ? ` Skipped: ${skipped.join(', ')}.` : '')
        : `Nothing matched this pipeline's inputs.${skipped.length ? ` Skipped: ${skipped.join(', ')}.` : ''}`,
    );
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
      const raw = values[key] ?? '';
      if (spec.required && !raw.trim()) {
        nextErrors[key] = 'This input is required.';
        continue;
      }
      if (!raw.trim()) continue;
      if (spec.type === 'json') {
        try {
          runInputs[key] = JSON.parse(raw);
        } catch {
          nextErrors[key] = 'Enter valid JSON.';
        }
      } else {
        runInputs[key] = raw;
      }
    }
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }

    setLaunching(true);
    setLaunchStage('Uploading files…');
    setLaunchError(null);
    try {
      for (const key of keys) {
        const spec = inputs[key];
        if (spec.type !== 'file') continue;
        const loaded = fileRefValues[key] ?? [];
        if (loaded.length > 0) {
          runInputs[key] = spec.multiple ? loaded : loaded[0];
          continue;
        }
        const selected = fileValues[key] ?? [];
        if (selected.length === 0) continue;
        const uploaded = await api.uploadWorkflowFiles(selected);
        runInputs[key] = spec.multiple ? uploaded.files : uploaded.files[0];
      }

      setLaunchStage('Checking pipeline…');
      const preflight = await api.validatePipeline(pipelineYaml, runInputs);
      if (!preflight.valid) {
        const msgs = preflight.issues
          .filter(i => i.severity === 'error')
          .slice(0, 6)
          .map(i => `${i.code}${i.node_id ? ` (${i.node_id})` : ''}: ${i.message}`);
        setLaunchError(`Pipeline blocked before running. ${msgs.join(' · ')}`);
        setLaunching(false);
        setLaunchStage(null);
        return;
      }

      const parsedPipeline = parsePipelineYaml(pipelineYaml);
      const firstStage = parsedPipeline.stages[0];
      if (!firstStage) {
        setLaunchError('This pipeline has no stages.');
        setLaunching(false);
        setLaunchStage(null);
        return;
      }
      setLaunchStage('Opening stage 1…');
      const { yaml: stageYaml } = await api.getWorkflow(firstStage.workflow);

      const pipelineRunId = crypto.randomUUID();
      const stageRunId = crypto.randomUUID();
      // Navigate straight into the live graph — it triggers the actual run
      // itself (opens the SSE stream first, same as a plain workflow run),
      // rather than us awaiting the whole stage here and landing on a
      // static status page after the fact. Pipelines are a business-user
      // flow, so this defaults to the Guided Run surface (Cockpit stays
      // reachable from there via "View process map").
      navigate(`/guided/${stageRunId}`, {
        state: {
          workflowYaml: stageYaml,
          workflowName: firstStage.id,
          inputs: runInputs,
          pipeline: {
            mode: 'start',
            pipelineYaml,
            pipelineRunId,
            pipelineName,
            stageId: firstStage.id,
            stageIndex: 0,
            totalStages: parsedPipeline.stages.length,
          },
        },
      });
    } catch (e: unknown) {
      setLaunchError(e instanceof Error ? e.message : String(e));
      setLaunching(false);
      setLaunchStage(null);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[85vh] overflow-y-auto">
        <div className="px-6 py-4 border-b border-slate-200">
          <h2 className="text-lg font-semibold">Run {pipelineName}</h2>
          <p className="text-xs text-ink-500 mt-0.5">
            This opens the live Cockpit view for stage 1. Each later stage
            waits for you to review its output and explicitly continue to
            the next stage's Cockpit view.
          </p>
        </div>
        <div className="px-6 py-4 space-y-4">
          {keys.length === 0 && (
            <div className="text-sm text-ink-500">This pipeline declares no inputs.</div>
          )}
          {keys.map((key, index) => {
            const spec = inputs[key];
            if (spec.type === 'file') {
              return (
                <FileInputField
                  key={key}
                  inputId={`pipeline-file-${index}`}
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
                    setFileValues(current => ({ ...current, [key]: nextFiles }));
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
                <label className="block text-sm font-medium text-ink-900">
                  {key}
                  {spec.required && <span className="text-red-500"> *</span>}
                </label>
                {spec.description && (
                  <p className="text-xs text-ink-500 mb-1">{spec.description}</p>
                )}
                <textarea
                  rows={spec.type === 'json' ? 6 : 3}
                  value={values[key] ?? ''}
                  onChange={e => setValues(v => ({ ...v, [key]: e.target.value }))}
                  className="mt-1 block w-full rounded-md border-slate-300 text-sm py-2 px-3 border font-mono"
                  placeholder={spec.type === 'json' ? '{ }' : ''}
                />
                {errors[key] && <p className="text-xs text-red-600 mt-1">{errors[key]}</p>}
              </div>
            );
          })}

          {keys.length > 0 && (
            <div className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-3">
              <button
                type="button"
                onClick={() => setImportOpen(o => !o)}
                className="text-xs font-medium text-accent-700 hover:underline"
              >
                {importOpen ? 'Hide' : 'Import inputs from JSON'}
              </button>
              {!importOpen && (
                <p className="mt-1 text-xs text-ink-500">
                  Paste JSON from Run History's "Copy run as workflow inputs"
                  to fill this form — matched by key, unmatched keys skipped.
                </p>
              )}
              {importOpen && (
                <div className="mt-2 space-y-2">
                  <textarea
                    rows={5}
                    value={importText}
                    onChange={e => setImportText(e.target.value)}
                    placeholder='{"inputName": "value", ...}'
                    className="block w-full rounded-md border-slate-300 text-xs py-2 px-3 border font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => applyImportedJson(importText)}
                    disabled={!importText.trim()}
                    className="px-3 py-1.5 rounded-md bg-accent-600 text-white text-xs hover:bg-accent-500 disabled:opacity-50"
                  >
                    Apply pasted JSON
                  </button>
                  {importMessage && <p className="text-xs text-ink-500">{importMessage}</p>}
                </div>
              )}
            </div>
          )}

          {launchError && (
            <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {launchError}
            </div>
          )}
        </div>
        <div className="px-6 py-4 border-t border-slate-200 flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={launching}
            className="px-4 py-2 rounded-md border border-slate-300 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={launch}
            disabled={launching}
            className="px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            {launching ? (launchStage ?? 'Starting…') : 'Run pipeline'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Library (list + launch) -----------------------------------------------
export function PipelineLibrary() {
  const navigate = useNavigate();
  const [pipelines, setPipelines] = useState<PipelineSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{
    name: string;
    yamlText: string;
    inputs: Record<string, WorkflowInputSpec>;
  } | null>(null);

  useEffect(() => {
    api.listPipelines().then(setPipelines).catch(e => setError(String(e)));
  }, []);

  async function onRun(name: string) {
    setRunError(null);
    try {
      const { yaml: yamlText } = await api.getPipeline(name);
      const parsed = parsePipelineYaml(yamlText);
      setDialog({ name, yamlText, inputs: parsed.inputs ?? {} });
    } catch (e: unknown) {
      setRunError(e instanceof Error ? e.message : String(e));
    }
  }

  if (error) return <div className="p-8 text-bad">Failed to load pipelines: {error}</div>;
  if (pipelines === null) return <div className="p-8"><Spinner label="Loading pipelines…" /></div>;

  const current = pipelines.find(p => p.name === selected) ?? null;

  return (
    <div className="h-full flex">
      <aside className="w-80 border-r border-slate-200 bg-white overflow-y-auto">
        <div className="p-4 border-b border-slate-200 flex items-center justify-between">
          <h2 className="font-medium">Pipelines</h2>
          <button
            onClick={() => navigate('/pipelines/runs')}
            className="text-sm px-3 py-1 rounded-md border border-slate-300 hover:bg-slate-50"
          >
            Runs
          </button>
        </div>
        {pipelines.length === 0 ? (
          <div className="p-4 text-sm text-ink-500">
            None saved yet — save one via <code>POST /api/pipelines/save</code>.
            A pipeline chains existing workflows: stage N+1's inputs get
            auto-matched from stage N's node outputs by name.
          </div>
        ) : (
          <ul>
            {pipelines.map(p => (
              <li key={p.name}>
                <button
                  onClick={() => setSelected(p.name)}
                  className={`w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 ${
                    selected === p.name ? 'bg-slate-100' : ''
                  }`}
                >
                  <div className="font-medium text-ink-900">{p.name}</div>
                  <div className="text-xs text-ink-500 mt-1 line-clamp-2">
                    {p.description || <i>No description.</i>}
                  </div>
                  <div className="text-xs text-ink-500 mt-1">{p.stage_count} stages</div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="flex-1 p-8">
        {current === null ? (
          <div className="text-ink-500">Select a pipeline to see its summary.</div>
        ) : (
          <div className="max-w-3xl">
            <h2 className="text-2xl font-semibold">{current.name}</h2>
            <p className="mt-3 text-ink-700">{current.description || <i>No description.</i>}</p>
            <div className="mt-2 text-sm text-ink-500">{current.stage_count} stages</div>
            <div className="mt-8">
              <button
                onClick={() => onRun(current.name)}
                className="px-4 py-2 rounded-md bg-accent-600 text-white hover:bg-accent-500"
              >
                Run
              </button>
            </div>
            {runError && <div className="mt-3 text-sm text-bad">Run failed to start: {runError}</div>}
          </div>
        )}
      </section>

      {dialog && (
        <PipelineLaunchDialog
          pipelineName={dialog.name}
          pipelineYaml={dialog.yamlText}
          inputs={dialog.inputs}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

// ---- Run list ---------------------------------------------------------------
export function PipelineRuns() {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<PipelineRunSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.pipelineRuns()
      .then(data => { if (!cancelled) setRuns(data.runs); })
      .catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, []);

  if (error) return <div className="p-8 text-bad">Couldn't load pipeline runs: {error}</div>;
  if (runs === null) return <div className="p-8"><Spinner label="Loading pipeline runs…" /></div>;

  return (
    <div className="p-8 max-w-3xl">
      <h2 className="text-xl font-semibold mb-4">Pipeline runs</h2>
      {runs.length === 0 ? (
        <div className="text-ink-500 text-sm">No pipeline runs yet.</div>
      ) : (
        <div className="border border-slate-200 rounded-lg divide-y divide-slate-100">
          {runs.map(r => (
            <button
              key={r.pipeline_run_id}
              onClick={() => navigate(`/pipelines/runs/${r.pipeline_run_id}`)}
              className="w-full text-left px-4 py-3 hover:bg-slate-50 flex items-center justify-between"
            >
              <div>
                <div className="font-medium text-ink-900">{r.pipeline_name}</div>
                <div className="font-mono text-xs text-ink-500">{r.pipeline_run_id}</div>
              </div>
              <div className="text-xs uppercase tracking-wide text-ink-500">
                {r.status} · stage {r.current_stage_index + 1}/{r.stages.length}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- Run detail (stage progress + advance) ---------------------------------
export function PipelineRunView() {
  const { pipelineRunId } = useParams<{ pipelineRunId: string }>();
  const navigate = useNavigate();
  const [detail, setDetail] = useState<PipelineRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [advancing, setAdvancing] = useState(false);
  const [advanceError, setAdvanceError] = useState<string | null>(null);
  const [abandoning, setAbandoning] = useState(false);
  const [abandonError, setAbandonError] = useState<string | null>(null);

  useEffect(() => {
    if (!pipelineRunId) return;
    let cancelled = false;
    const load = () => {
      api.pipelineRunDetail(pipelineRunId)
        .then(data => { if (!cancelled) setDetail(data); })
        .catch(e => { if (!cancelled) setError(String(e)); });
    };
    load();
    const timer = window.setInterval(load, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [pipelineRunId]);

  async function advance() {
    if (!pipelineRunId || !detail || !nextStage) return;
    setAdvancing(true);
    setAdvanceError(null);
    try {
      // Same pattern as launching a pipeline: open the next stage's live
      // run view first (defaults to Guided Run, same as launch), and let it
      // trigger the advance call itself.
      const { yaml: stageYaml } = await api.getWorkflow(nextStage.workflow);
      const stageRunId = crypto.randomUUID();
      navigate(`/guided/${stageRunId}`, {
        state: {
          workflowYaml: stageYaml,
          workflowName: nextStage.id,
          pipeline: {
            mode: 'advance',
            pipelineRunId,
            pipelineName: detail.pipeline_name,
            stageId: nextStage.id,
            stageIndex: detail.current_stage_index + 1,
            totalStages: detail.stages.length,
          },
        },
      });
    } catch (e: unknown) {
      setAdvanceError(e instanceof Error ? e.message : String(e));
      setAdvancing(false);
    }
  }

  async function abandon() {
    if (!pipelineRunId) return;
    setAbandoning(true);
    setAbandonError(null);
    try {
      await api.abandonPipeline(pipelineRunId);
      setDetail(current => (current ? { ...current, status: 'abandoned' } : current));
    } catch (e: unknown) {
      setAbandonError(e instanceof Error ? e.message : String(e));
    } finally {
      setAbandoning(false);
    }
  }

  if (error) return <div className="p-8 text-bad">Couldn't load this pipeline run: {error}</div>;
  if (detail === null) return <div className="p-8"><Spinner label="Loading pipeline run…" /></div>;

  const nextStage = detail.status === 'gated' ? detail.stages[detail.current_stage_index + 1] : null;
  const canAbandon = detail.status === 'running' || detail.status === 'gated';

  return (
    <div className="p-8 max-w-3xl">
      <div className="flex items-baseline justify-between">
        <div>
          <h2 className="text-xl font-semibold">{detail.pipeline_name}</h2>
          <div className="font-mono text-xs text-ink-500 mt-0.5">{detail.pipeline_run_id}</div>
        </div>
        <CopyButton
          text={JSON.stringify(detail.pipeline_inputs, null, 2)}
          label="Copy pipeline inputs as JSON"
        />
      </div>

      <div className="mt-2 flex items-center justify-between">
        <div className="text-sm uppercase tracking-wide text-ink-500">
          Status: {detail.status}
        </div>
        {canAbandon && (
          <button
            onClick={abandon}
            disabled={abandoning}
            title="Manually mark this pipeline as abandoned so its stage run can be deleted."
            className="px-3 py-1.5 rounded-md border border-red-300 text-xs text-red-700 hover:bg-red-50 disabled:opacity-50"
          >
            {abandoning ? 'Abandoning…' : 'Abandon pipeline'}
          </button>
        )}
      </div>
      {abandonError && (
        <div className="mt-2 text-xs text-red-700">{abandonError}</div>
      )}

      {detail.status === 'gated' && nextStage && (
        <div className="mt-5 rounded-md border border-cyan-200 bg-cyan-50 px-4 py-3">
          <div className="text-sm text-cyan-900">
            Stage {detail.current_stage_index + 1} finished. Review its output
            below, then continue to <b>{nextStage.id}</b>.
          </div>
          <button
            onClick={advance}
            disabled={advancing}
            className="mt-2 px-4 py-2 rounded-md bg-accent-600 text-white text-sm hover:bg-accent-500 disabled:opacity-50"
          >
            {advancing ? `Opening ${nextStage.id}…` : `Continue to ${nextStage.id}`}
          </button>
          {advanceError && <p className="mt-2 text-xs text-red-700">{advanceError}</p>}
        </div>
      )}

      {detail.status === 'failed' && (
        <div className="mt-5 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          Pipeline stopped — a stage failed or was rejected. Open that stage
          below to see the error and retry it from Run History; the pipeline
          isn't currently resumable from here after a retry (retry the stage,
          then continue this pipeline manually stage by stage via
          Run History).
        </div>
      )}

      {detail.status === 'completed' && (
        <div className="mt-5 rounded-md border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
          All stages completed.
        </div>
      )}

      {detail.status === 'abandoned' && (
        <div className="mt-5 rounded-md border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-ink-700">
          This pipeline was manually abandoned. Its stage runs can now be
          deleted from Run History.
        </div>
      )}

      <div className="mt-6 space-y-2">
        {detail.stages.map((stage, index) => (
          <div
            key={stage.id}
            className={`border rounded-lg px-4 py-3 ${
              index === detail.current_stage_index ? 'border-accent-300' : 'border-slate-200'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className={`h-2.5 w-2.5 rounded-full ${STAGE_STATUS_DOT[stage.status]}`} />
                <span className="font-medium text-ink-900">{stage.id}</span>
                <span className="text-xs text-ink-500">({stage.workflow})</span>
              </div>
              <span className="text-xs uppercase tracking-wide text-ink-500">
                {STAGE_STATUS_LABEL[stage.status]}
              </span>
            </div>
            {stage.error && (
              <div className="mt-2 text-xs text-red-700 font-mono">{stage.error}</div>
            )}
            {stage.run_id && (
              <button
                onClick={() => navigate(`/history/${stage.run_id}`)}
                className="mt-2 text-xs text-accent-600 hover:underline"
              >
                View stage details / outputs →
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
