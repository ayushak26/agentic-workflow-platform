import type { NodeTypeManifest } from '../../../api/types';
import { ExecutionKindBadge } from './ExecutionKindBadge';

/**
 * The About tab (§19).
 *
 * Answers the questions a non-technical author actually has about a step —
 * what it does, why you would use it, what it receives, what it produces, and
 * whether it uses a model or touches the outside world — without them reading
 * the node's source or guessing from a config form.
 *
 * Content comes from the node type's own declaration in the registry, so it
 * cannot go stale relative to what the node really does.
 */

export function AboutPanel({
  manifest,
  businessLabel,
  nodeId,
  onBusinessLabelChange,
}: {
  manifest: NodeTypeManifest | undefined;
  businessLabel: string;
  nodeId: string;
  onBusinessLabelChange: (label: string) => void;
}) {
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

      <div className="mt-3 flex items-center gap-2">
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

      <dl className="mt-4 space-y-3">
        <Entry label="What this step does" value={about.what ?? manifest.description} />
        <Entry label="Why it is used" value={about.why} />
        <Entry label="What it receives" value={about.receives} />
        <Entry label="What it produces" value={about.produces} />
      </dl>

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
