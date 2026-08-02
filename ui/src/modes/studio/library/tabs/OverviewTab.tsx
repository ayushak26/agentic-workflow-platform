import type { LibraryMetadata, ReadinessSummary, WorkflowSummary } from '../../../../api/types';
import { buildGuidedRuntimeModel } from '../../guided/runtime-model';
import type { YamlWorkflow } from '../../yaml-bridge';
import { READINESS_LABEL } from '../readiness';

const STATUS_LABEL: Record<string, string> = {
  approved: 'Approved',
  draft: 'Draft',
  in_review: 'In review',
  deprecated: 'Deprecated',
  archived: 'Archived',
};

function durationText(library: LibraryMetadata): string {
  const duration = library.typical_duration;
  if (!duration || (duration.minimum_minutes == null && duration.maximum_minutes == null)) {
    return 'Not enough information yet';
  }
  const { minimum_minutes: min, maximum_minutes: max } = duration;
  if (min != null && max != null && min !== max) return `${min}–${max} minutes`;
  return `${max ?? min} minutes`;
}

export function OverviewTab({
  workflow,
  library,
  readiness,
  parsed,
  onPrepareRun,
}: {
  workflow: WorkflowSummary;
  library: LibraryMetadata;
  readiness: ReadinessSummary;
  parsed: YamlWorkflow;
  onPrepareRun: () => void;
}) {
  const model = buildGuidedRuntimeModel({
    workflow: parsed,
    nodeStatuses: {},
    outputs: {},
  });

  return (
    <div className="library-tab-content">
      <p className="library-overview-objective">
        {library.summary || 'Description not yet provided.'}
      </p>

      {library.suitable_for.length > 0 && (
        <div className="library-overview-row">
          <span>Best suited for</span>
          <p>{library.suitable_for.join(', ')}</p>
        </div>
      )}
      {library.not_suitable_for.length > 0 && (
        <div className="library-overview-row">
          <span>Not suited for</span>
          <p>{library.not_suitable_for.join(', ')}</p>
        </div>
      )}

      <div className="library-overview-facts">
        <div>
          <span>Status</span>
          <strong>{STATUS_LABEL[library.visibility_status] ?? library.visibility_status}</strong>
        </div>
        <div>
          <span>Environment readiness</span>
          <strong className={`library-readiness-inline is-${readiness.level}`}>
            {READINESS_LABEL[readiness.level]}
          </strong>
        </div>
        <div>
          <span>Typical duration</span>
          <strong>{durationText(library)}</strong>
        </div>
        <div>
          <span>Human review points</span>
          <strong>{library.human_reviews.count}</strong>
        </div>
        <div>
          <span>Current version</span>
          <strong>{workflow.version}</strong>
        </div>
        <div>
          <span>Last updated</span>
          <strong>{new Date(workflow.updated_at).toLocaleDateString()}</strong>
        </div>
      </div>

      {!library.declared && (
        <div className="library-overview-note">
          This workflow hasn&apos;t been given business-language Library metadata yet.
          The summary and duration above are best-effort fallbacks derived from
          its YAML — open it in the Builder to author real Library details.
        </div>
      )}

      {readiness.level !== 'ready' && (
        <div className={`library-readiness-banner is-${readiness.level}`}>
          <strong>{READINESS_LABEL[readiness.level]}</strong>
          <ul>
            {readiness.items.slice(0, 4).map((item, index) => (
              <li key={`${item.code}-${index}`}>
                {item.message}
                {item.suggestion ? ` ${item.suggestion}` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="library-outcome-sequence" aria-label="Outcome sequence">
        {model.stages.map((stage, index) => (
          <span key={stage.id} className="library-outcome-step">
            {stage.displayName}
            {index < model.stages.length - 1 && <span aria-hidden="true"> → </span>}
          </span>
        ))}
      </div>

      <button
        type="button"
        className="ui-button ui-button--primary library-overview-cta"
        disabled={readiness.level === 'blocked'}
        onClick={onPrepareRun}
      >
        Prepare and run
      </button>
    </div>
  );
}
