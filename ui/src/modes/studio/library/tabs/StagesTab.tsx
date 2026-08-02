import { useState } from 'react';
import { buildGuidedRuntimeModel } from '../../guided/runtime-model';
import type { YamlWorkflow } from '../../yaml-bridge';

export function StagesTab({ parsed }: { parsed: YamlWorkflow }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // No live run — this is a structural preview of the same stage/step model
  // Guided Run uses at execution time, computed with every node "pending".
  const model = buildGuidedRuntimeModel({
    workflow: parsed,
    nodeStatuses: {},
    outputs: {},
  });

  function toggle(stageId: string) {
    setExpanded(current => {
      const next = new Set(current);
      if (next.has(stageId)) next.delete(stageId); else next.add(stageId);
      return next;
    });
  }

  return (
    <div className="library-tab-content">
      <p className="library-empty-note">
        Business stages, not individual nodes — only steps that clarify
        responsibility or review independence show a role.
      </p>
      <ol className="library-stage-list">
        {model.stages.map((stage, index) => {
          const steps = model.steps.filter(step => step.stageId === stage.id);
          const reviewSteps = steps.filter(step => step.role || step.showRole || step.recoveryActions.length > 0);
          const isOpen = expanded.has(stage.id);
          return (
            <li key={stage.id} className="library-stage-item">
              <button type="button" className="library-stage-header" onClick={() => toggle(stage.id)}>
                <span className="library-stage-index">{index + 1}</span>
                <span className="library-stage-heading">
                  <strong>Stage {index + 1} — {stage.displayName}</strong>
                  <span>{stage.purpose}</span>
                </span>
                <span aria-hidden="true">{isOpen ? '−' : '+'}</span>
              </button>
              {isOpen && (
                <div className="library-stage-body">
                  <ul className="library-stage-steps">
                    {steps.map(step => (
                      <li key={step.id}>
                        {step.displayName}
                        {step.showRole && step.role && (
                          <span className="library-stage-role"> · {step.role}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                  {steps.some(step => step.recoveryActions.length > 0 || step.failureMessage) && (
                    <div className="library-stage-review">
                      Human review: {
                        steps.find(step => step.failureMessage)?.failureMessage
                        ?? 'Approve this stage\'s output before the workflow continues.'
                      }
                    </div>
                  )}
                  {reviewSteps.length === 0 && (
                    <div className="library-stage-independent">
                      This stage can complete independently — no review checkpoint.
                    </div>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
