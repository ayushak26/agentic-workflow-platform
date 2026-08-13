import { useCallback, useMemo, useState } from 'react';

import { api } from '../../../api/client';
import type { SimulationResult, SimulationStep } from '../../../api/types';
import type { YamlWorkflow } from '../yaml-bridge';
import { ExecutionKindBadge } from './ExecutionKindBadge';
import { ExplanationView, ValueTree } from './ExplanationView';

/**
 * The workflow simulator (§23, §24, §44).
 *
 * Paste one real customer message, press Run, and watch the process execute:
 * which steps ran, in what order, what each decided, and — for every
 * deterministic step — exactly which conditions made it decide that.
 *
 * The "override a step's result" control is what makes the decisive
 * demonstration possible in seconds rather than by hunting for an input the
 * model happens to be unsure about: freeze the extraction's confidence at 0.64,
 * rerun, and the graph visibly routes to Human Review instead of to Support.
 * Nothing about the workflow changed — which is the point.
 *
 * The simulation runs the real runtime (same compiler, same nodes, same
 * preflight); it just skips the durable run record, so it never pollutes Run
 * History.
 */

export function SimulatorPanel({
  workflow,
  workflowYaml,
  onHighlightPath,
  onSelectNode,
}: {
  workflow: YamlWorkflow;
  workflowYaml: string;
  /** Lights up the executed path on the canvas. */
  onHighlightPath: (path: string[], waiting: string[]) => void;
  onSelectNode: (nodeId: string) => void;
}) {
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [showOverrides, setShowOverrides] = useState(false);
  const [result, setResult] = useState<SimulationResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openStep, setOpenStep] = useState<string | null>(null);

  const inputSpecs = useMemo(
    () => Object.entries(workflow.inputs ?? {}),
    [workflow.inputs],
  );

  const stubOutputs = useMemo(() => {
    const parsed: Record<string, Record<string, unknown>> = {};
    for (const [nodeId, text] of Object.entries(overrides)) {
      if (!text.trim()) continue;
      try {
        const value = JSON.parse(text);
        if (value && typeof value === 'object') {
          parsed[nodeId] = value as Record<string, unknown>;
        }
      } catch {
        // A half-typed override is not an error worth interrupting for; the
        // run simply proceeds without it and the step executes normally.
      }
    }
    return parsed;
  }, [overrides]);

  const run = useCallback(() => {
    setBusy(true);
    setError(null);
    setResult(null);
    api.simulateWorkflow({
      workflow_yaml: workflowYaml,
      inputs,
      stub_outputs: stubOutputs,
    })
      .then(simulation => {
        setResult(simulation);
        onHighlightPath(simulation.path ?? [], simulation.waiting_for ?? []);
        if (simulation.error) setError(simulation.error);
      })
      .catch(reason => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setBusy(false));
  }, [inputs, onHighlightPath, stubOutputs, workflowYaml]);

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="builder-panel-heading">Run a simulation</div>
      <p className="mt-1 text-[11px] leading-4 text-ink-500">
        Runs the real workflow against one example and shows what each step
        decided and why. No run record is created.
      </p>

      <section className="mt-3 space-y-2">
        {inputSpecs.length === 0 && (
          <div className="rounded-md border border-dashed border-slate-300 p-3 text-center text-[11px] text-ink-500">
            This workflow declares no inputs yet.
          </div>
        )}
        {inputSpecs.map(([name, spec]) => (
          <label className="block text-[11px] font-medium text-ink-700" key={name}>
            {name}
            {spec.required && <span className="ml-1 text-red-500">*</span>}
            {spec.description && (
              <span className="block text-[10px] font-normal text-ink-500">
                {spec.description}
              </span>
            )}
            <textarea
              className="builder-field mt-1"
              onChange={event => setInputs(current => ({
                ...current,
                [name]: event.target.value,
              }))}
              placeholder={
                name === 'message'
                  ? 'Bonjour,\n\nour Dura 25 pump stopped this morning. Die Seriennummer ist 77392.\nCan somebody call us urgently?'
                  : ''
              }
              rows={name === 'message' ? 6 : 2}
              value={inputs[name] ?? ''}
            />
          </label>
        ))}
      </section>

      <section className="mt-3">
        <button
          className="text-[11px] font-medium text-accent-700 hover:underline"
          onClick={() => setShowOverrides(value => !value)}
          type="button"
        >
          {showOverrides ? 'Hide' : 'Show'} step overrides
        </button>
        {showOverrides && (
          <div className="mt-2 rounded-md border border-slate-200 p-2">
            <p className="text-[10px] leading-4 text-ink-500">
              Freeze a step&apos;s result instead of running it. Useful to
              demonstrate what happens on a case the model rarely produces —
              set a low confidence and watch the routing change.
            </p>
            <div className="mt-2 space-y-2">
              {workflow.nodes.map(node => (
                <label className="block text-[10px] text-ink-700" key={node.id}>
                  <span className="font-mono">{node.id}</span>
                  <textarea
                    className="builder-field mt-0.5 font-mono"
                    onChange={event => setOverrides(current => ({
                      ...current,
                      [node.id]: event.target.value,
                    }))}
                    placeholder='{"confidence": 0.64}'
                    rows={2}
                    value={overrides[node.id] ?? ''}
                  />
                </label>
              ))}
            </div>
          </div>
        )}
      </section>

      <button
        className="ui-button ui-button--primary mt-3 w-full justify-center"
        disabled={busy}
        onClick={run}
        type="button"
      >
        {busy ? 'Running…' : 'Run simulation'}
      </button>

      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-[11px] leading-4 text-red-800">
          {error}
        </div>
      )}

      {result && (
        <section className="mt-4">
          <div className="flex items-center justify-between">
            <div className="text-[11px] font-semibold text-ink-800">
              {result.steps.length} step{result.steps.length === 1 ? '' : 's'} ran
            </div>
            <span className="text-[10px] text-ink-500">
              {result.status} · {result.duration_s}s
            </span>
          </div>

          <div className="mt-2 space-y-1.5">
            {result.steps.map((step, index) => (
              <StepCard
                index={index}
                key={`${step.node_id}-${index}`}
                onSelect={() => onSelectNode(step.node_id)}
                onToggle={() => setOpenStep(
                  openStep === step.node_id ? null : step.node_id,
                )}
                open={openStep === step.node_id}
                step={step}
              />
            ))}
          </div>

          {result.stubbed && result.stubbed.length > 0 && (
            <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-2 text-[10px] text-ink-600">
              Frozen for this run: {result.stubbed.join(', ')}. Those steps did
              not call anything.
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function StepCard({
  index,
  onSelect,
  onToggle,
  open,
  step,
}: {
  index: number;
  onSelect: () => void;
  onToggle: () => void;
  open: boolean;
  step: SimulationStep;
}) {
  const waiting = step.status === 'waiting';
  return (
    <div
      className={`rounded-md border bg-white ${
        waiting ? 'border-sky-300' : 'border-slate-200'
      }`}
    >
      <div className="flex items-center gap-2 px-2 py-1.5">
        <span className="flex h-5 w-5 flex-none items-center justify-center rounded-full bg-slate-100 text-[10px] font-semibold text-ink-600">
          {index + 1}
        </span>
        <button
          className="min-w-0 flex-1 text-left"
          onClick={onSelect}
          type="button"
        >
          <span className="block truncate text-[11px] font-semibold text-ink-900">
            {step.label}
          </span>
          <span className="block truncate font-mono text-[10px] text-ink-500">
            {step.node_id}
          </span>
        </button>
        <ExecutionKindBadge kind={step.execution_kind} />
        {step.stubbed && (
          <span className="flex-none rounded bg-slate-100 px-1 text-[9px] text-ink-500">
            frozen
          </span>
        )}
        {waiting && (
          <span className="flex-none rounded bg-sky-100 px-1 text-[9px] text-sky-700">
            waiting
          </span>
        )}
        <button
          aria-label={open ? 'Hide detail' : 'Show detail'}
          className="flex-none text-ink-400 hover:text-ink-800"
          onClick={onToggle}
          type="button"
        >
          {open ? '▾' : '▸'}
        </button>
      </div>

      <div className="border-t border-slate-100 px-2 py-1.5">
        <ExplanationView compact={!open} explanation={step.explanation} />
      </div>

      {open && step.output && (
        <div className="border-t border-slate-100 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            Output
          </div>
          <div className="mt-1">
            <ValueTree value={step.output} />
          </div>
        </div>
      )}

      {open && step.review && (
        <div className="border-t border-slate-100 px-2 py-1.5">
          <div className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            What the reviewer sees
          </div>
          <div className="mt-1">
            <ValueTree value={step.review} />
          </div>
        </div>
      )}
    </div>
  );
}
