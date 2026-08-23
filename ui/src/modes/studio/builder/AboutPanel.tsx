import { useState } from 'react';
import type { NodeTypeManifest } from '../../../api/types';
import { AskAiDialog } from './AskAiDialog';
import { ExecutionKindBadge } from './ExecutionKindBadge';

/**
 * The About tab (§19).
 *
 * Answers the questions a non-technical author actually has about a step —
 * what it does, why you would use it, what it receives, what it produces, and
 * whether it uses a model or touches the outside world — without them reading
 * the node's source or guessing from a config form.
 *
 * Content comes from the node type's own declaration in the registry, plus
 * whatever app/nodes/about_synthesis.py auto-derived for it (typical
 * upstream/downstream neighbours, important config, when/when-not to use),
 * so a specialized node type that never hand-authored an `about` still shows
 * something useful. "Ask AI" hands the model this exact step — its type, id,
 * and its real upstream/downstream neighbours on THIS canvas — rather than
 * the whole workflow.
 */

export function AboutPanel({
  businessLabel,
  completedMessage,
  downstreamTypes = [],
  manifest,
  nodeId,
  onBusinessLabelChange,
  onCompletedMessageChange,
  onRunningMessageChange,
  runningMessage,
  upstreamTypes = [],
}: {
  businessLabel: string;
  completedMessage: string;
  downstreamTypes?: string[];
  manifest: NodeTypeManifest | undefined;
  nodeId: string;
  onBusinessLabelChange: (label: string) => void;
  onCompletedMessageChange: (message: string) => void;
  onRunningMessageChange: (message: string) => void;
  runningMessage: string;
  upstreamTypes?: string[];
}) {
  const [askingAi, setAskingAi] = useState(false);

  if (!manifest) {
    return (
      <div className="p-5 text-sm text-ink-500">
        No description is registered for this step type.
      </div>
    );
  }

  const about = manifest.about ?? {};

  return (
    <div className="builder-inspector-scroll p-4">
      {/* Business name and technical type shown together (§17): the label is
          what the canvas and every reviewer read; the type is what the runtime
          executes. Editing one never changes the other. */}
      <label className="block text-[11px] font-medium text-ink-700">
        Business name
        <input
          className="builder-field mt-1"
          onChange={event => onBusinessLabelChange(event.target.value)}
          placeholder={nodeId}
          value={businessLabel}
        />
      </label>
      <p className="mt-1 text-[10px] text-ink-500">
        What this step is called on the canvas and in run explanations. Name the
        business action, not the technology.
      </p>

      {/* Chat progress copy (§26): shown in Business Chat while this step
          runs and once it finishes. Optional — the chat falls back to the
          business name when left empty. */}
      <label className="mt-4 block text-[11px] font-medium text-ink-700">
        Progress message (chat, while running)
        <input
          className="builder-field mt-1"
          onChange={event => onRunningMessageChange(event.target.value)}
          placeholder={`Working on: ${businessLabel || nodeId}…`}
          value={runningMessage}
        />
      </label>
      <label className="mt-3 block text-[11px] font-medium text-ink-700">
        Completed message (chat, when done)
        <input
          className="builder-field mt-1"
          onChange={event => onCompletedMessageChange(event.target.value)}
          placeholder={businessLabel || nodeId}
          value={completedMessage}
        />
      </label>
      <p className="mt-1 text-[10px] text-ink-500">
        Written for the business user chatting with this workflow. Keep both
        short; leave empty to use the business name.
      </p>

      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-slate-100 px-2 py-0.5 font-mono text-[10px] text-ink-600">
            {manifest.type_name}
          </span>
          <ExecutionKindBadge kind={manifest.execution_kind} />
          {manifest.family === 'core' && (
            <span className="rounded-full border border-accent-200 bg-accent-50 px-1.5 py-0.5 text-[9px] font-medium text-accent-700">
              Core building block
            </span>
          )}
        </div>
        <button
          className="flex-none rounded border border-slate-200 px-2 py-0.5 text-[10px] font-medium text-accent-700 hover:bg-accent-50"
          onClick={() => setAskingAi(true)}
          type="button"
        >
          Ask AI
        </button>
      </div>

      <dl className="mt-4 space-y-3">
        <Entry label="What this step does" value={about.what ?? manifest.description} />
        <Entry label="Why it is used" value={about.why} />
        <Entry label="What it receives" value={about.receives} />
        <Entry label="What it produces" value={about.produces} />
        <Entry label="When to use it" value={about.when_to_use} />
        <Entry label="When not to use it" value={about.when_not_to_use} />
        <Entry label="Example" value={about.example} />
      </dl>

      {(about.important_config ?? []).length > 0 && (
        <div className="mt-3">
          <dt className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
            Important configuration
          </dt>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {about.important_config!.map(field => <Badge key={field}>{field}</Badge>)}
          </div>
        </div>
      )}

      {((about.typical_upstream ?? []).length > 0 || (about.typical_downstream ?? []).length > 0) && (
        <div className="mt-3 grid grid-cols-2 gap-3">
          {(about.typical_upstream ?? []).length > 0 && (
            <div>
              <dt className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">Typically follows</dt>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {about.typical_upstream!.map(type => <Badge key={type}>{type}</Badge>)}
              </div>
            </div>
          )}
          {(about.typical_downstream ?? []).length > 0 && (
            <div>
              <dt className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">Typically precedes</dt>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {about.typical_downstream!.map(type => <Badge key={type}>{type}</Badge>)}
              </div>
            </div>
          )}
        </div>
      )}

      {about.safety && (
        <div className="mt-4 rounded-md border border-amber-200 bg-amber-50 p-2 text-[11px] leading-4 text-amber-900">
          <div className="font-semibold">Safety</div>
          <div className="mt-0.5">{about.safety}</div>
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-2 text-[10px]">
        <Flag
          label={manifest.uses_ai ? 'Uses a model' : 'No model call'}
          tone={manifest.uses_ai ? 'ai' : 'plain'}
        />
        <Flag
          label={
            manifest.external_action
              ? 'Acts outside the platform'
              : 'Changes nothing outside the workflow'
          }
          tone={manifest.external_action ? 'warn' : 'plain'}
        />
      </div>

      {askingAi && (
        <AskAiDialog
          context={{
            node_type: manifest.type_name,
            node_id: nodeId,
            relevant_upstream_nodes: upstreamTypes,
            relevant_downstream_nodes: downstreamTypes,
          }}
          onClose={() => setAskingAi(false)}
          starterQuestion={`Explain the "${nodeId}" step (a ${manifest.type_name}) — what it does, how it's configured, and how it fits with its neighbours on this canvas.`}
          title={`Ask AI — ${businessLabel || nodeId}`}
        />
      )}
    </div>
  );
}

function Entry({ label, value }: { label: string; value?: string }) {
  if (!value) return null;
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-wide text-ink-500">
        {label}
      </dt>
      <dd className="mt-0.5 text-[11px] leading-4 text-ink-700">{value}</dd>
    </div>
  );
}

function Badge({ children }: { children: string }) {
  return (
    <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-ink-600">
      {children}
    </span>
  );
}

function Flag({ label, tone }: { label: string; tone: 'ai' | 'warn' | 'plain' }) {
  const className = tone === 'ai'
    ? 'border-violet-200 bg-violet-50 text-violet-700'
    : tone === 'warn'
      ? 'border-amber-200 bg-amber-50 text-amber-800'
      : 'border-slate-200 bg-slate-50 text-ink-600';
  return (
    <span className={`rounded-full border px-2 py-0.5 ${className}`}>{label}</span>
  );
}
