import {
  WORKFLOW_STATUS_CLASS,
  WORKFLOW_STATUS_GLYPH,
  WORKFLOW_STATUS_LABEL,
  type WorkflowStatus,
} from './workflowStatus';

export type WorkflowProgressStep = {
  id: string;
  name: string;
  type?: string;
  status: WorkflowStatus;
  inputSummary?: string;
  outputSummary?: string;
  tool?: string;
  error?: string;
};

export function WorkflowProgress({
  steps,
  selectedStepId,
  onSelectStep,
  compact = false,
  showSummary = true,
}: {
  steps: WorkflowProgressStep[];
  selectedStepId?: string | null;
  onSelectStep?: (stepId: string) => void;
  compact?: boolean;
  showSummary?: boolean;
}) {
  const complete = steps.filter(step => step.status === 'done').length;

  return (
    <div aria-label="Workflow progress">
      {showSummary && (
        <div className="mb-2 text-xs font-medium text-ink-700">
          {complete} of {steps.length} steps complete
        </div>
      )}
      <div className={compact ? 'flex items-center gap-1 overflow-x-auto py-1' : 'space-y-1.5'}>
        {steps.map((step, index) => {
          const selected = selectedStepId === step.id;
          const button = (
            <button
              type="button"
              title={`${step.name}: ${WORKFLOW_STATUS_LABEL[step.status]}`}
              onClick={() => onSelectStep?.(step.id)}
              className={`${compact ? 'whitespace-nowrap rounded-full px-2 py-1 text-[11px]' : 'flex w-full items-start gap-2 rounded-lg px-2.5 py-2 text-left'} ${
                selected ? 'bg-accent-50 text-accent-800 ring-1 ring-accent-200' : 'text-ink-600 hover:bg-slate-50'
              }`}
            >
              <span className={`${compact ? '' : 'mt-0.5 w-4 text-center'} ${WORKFLOW_STATUS_CLASS[step.status]}`}>
                {WORKFLOW_STATUS_GLYPH[step.status]}
              </span>
              <span className={compact ? '' : 'min-w-0 flex-1'}>
                <span className={compact ? '' : 'block truncate text-xs font-medium text-ink-800'}>{step.name}</span>
                {!compact && (
                  <span className="block truncate text-[11px] text-ink-400">
                    {step.type ? `${step.type} · ` : ''}{WORKFLOW_STATUS_LABEL[step.status]}
                  </span>
                )}
              </span>
            </button>
          );
          return compact ? (
            <div key={step.id} className="flex items-center gap-1">
              {index > 0 && <span className="text-ink-300">→</span>}
              {button}
            </div>
          ) : <div key={step.id}>{button}</div>;
        })}
      </div>
    </div>
  );
}