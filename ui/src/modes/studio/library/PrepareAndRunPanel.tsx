import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from '../../../api/client';
import type { ReadinessSummary, WorkflowSummary } from '../../../api/types';
import { Spinner } from '../../../components/Spinner';
import { humanizeIdentifier } from '../guided/runtime-model';
import { RunDialog } from '../RunDialog';
import { isChatbotStart, parseYaml, type YamlWorkflow } from '../yaml-bridge';
import { READINESS_LABEL, readinessFromPreflight } from './readiness';

type Step = 'review' | 'inputs';

export function PrepareAndRunPanel({
  workflow,
  onClose,
}: {
  workflow: WorkflowSummary;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [step, setStep] = useState<Step>('review');
  const [yamlText, setYamlText] = useState<string | null>(null);
  const [parsed, setParsed] = useState<YamlWorkflow | null>(null);
  const [readiness, setReadiness] = useState<ReadinessSummary>(workflow.readiness);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api.getWorkflow(workflow.name)
      .then(({ yaml }) => {
        if (cancelled) return;
        setYamlText(yaml);
        setParsed(parseYaml(yaml));
      })
      .catch(reason => setError(String(reason)));
    return () => { cancelled = true; };
  }, [workflow.name]);

  async function checkReadinessNow() {
    if (!yamlText) return;
    setChecking(true);
    setError(null);
    try {
      // The full service-probing check (check_services=true) — same gate
      // RunDialog itself runs right before launch — so "Ready" here means
      // genuinely ready, not just structurally valid.
      const report = await api.validateWorkflow(yamlText, undefined, true);
      setReadiness(readinessFromPreflight(report));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setChecking(false);
    }
  }

  async function startNow() {
    if (!yamlText) return;
    setLaunching(true);
    setError(null);
    try {
      const runId = crypto.randomUUID();
      navigate(`/business/${runId}`, {
        state: { workflowYaml: yamlText, workflowName: workflow.name, inputs: {} },
      });
    } finally {
      setLaunching(false);
    }
  }

  if (step === 'inputs' && yamlText && parsed) {
    return (
      <RunDialog
        workflowName={workflow.name}
        workflowYaml={yamlText}
        inputs={parsed.inputs ?? {}}
        onClose={onClose}
      />
    );
  }

  const title = workflow.library?.title || humanizeIdentifier(workflow.name);
  const inputCount = parsed ? Object.keys(parsed.inputs ?? {}).length : 0;
  // A chatbot-mode Start always needs its message collected, even when
  // `parsed.inputs` is empty (see isChatbotStart's own doc comment) — this
  // used to fall through to "no inputs needed" and skip straight to a
  // Start(); an empty chat message, launching every run with no message
  // at all for any chatbot-mode workflow with no legacy top-level
  // `inputs:` block.
  const needsInputsStep = inputCount > 0 || (parsed ? isChatbotStart(parsed) : false);
  const reviewCount = workflow.library?.human_reviews.count ?? 0;
  const blocked = readiness.level === 'blocked';

  return (
    <div className="fixed inset-0 bg-black/30 flex items-center justify-center z-50">
      <div className="prepare-run-panel">
        <div className="prepare-run-header">
          <div>
            <div className="library-details-eyebrow">Prepare and run</div>
            <h2>{title}</h2>
          </div>
          <button type="button" aria-label="Cancel" onClick={onClose}>×</button>
        </div>

        {!parsed ? (
          <div className="prepare-run-body"><Spinner label="Loading workflow…" /></div>
        ) : (
          <div className="prepare-run-body">
            <section className="prepare-run-step">
              <h3>1. Confirm objective</h3>
              <p>{workflow.library?.summary || workflow.description || 'Description not yet provided.'}</p>
            </section>

            <section className="prepare-run-step">
              <h3>2. What you&apos;ll need</h3>
              {!needsInputsStep ? (
                <p>No inputs are required — this workflow starts immediately.</p>
              ) : inputCount === 0 ? (
                <p>A message to provide on the next step.</p>
              ) : (
                <p>{inputCount} input{inputCount === 1 ? '' : 's'} to provide on the next step.</p>
              )}
            </section>

            <section className="prepare-run-step">
              <h3>3. Review participants</h3>
              {reviewCount > 0 ? (
                <ul>
                  {(workflow.library?.human_reviews.labels ?? []).map((label, index) => (
                    <li key={index}>{label}</li>
                  ))}
                  {(workflow.library?.human_reviews.labels ?? []).length === 0 && (
                    <li>{reviewCount} review point{reviewCount === 1 ? '' : 's'} in this workflow.</li>
                  )}
                </ul>
              ) : (
                <p>This workflow completes independently — no review checkpoint is expected.</p>
              )}
            </section>

            <section className="prepare-run-step">
              <h3>4. Check readiness</h3>
              <div className={`library-readiness-banner is-${readiness.level}`}>
                <strong>{READINESS_LABEL[readiness.level]}</strong>
                {readiness.items.length > 0 && (
                  <ul>
                    {readiness.items.map((item, index) => (
                      <li key={`${item.code}-${index}`}>
                        {item.message}
                        {item.suggestion ? ` ${item.suggestion}` : ''}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              <button type="button" className="ui-button ui-button--secondary" onClick={checkReadinessNow} disabled={checking}>
                {checking ? 'Checking…' : 'Re-check readiness'}
              </button>
            </section>

            {error && <div className="library-details-error">{error}</div>}

            <div className="prepare-run-actions">
              <button type="button" className="ui-button ui-button--secondary" onClick={onClose}>
                Cancel
              </button>
              <button
                type="button"
                className="ui-button ui-button--primary"
                disabled={blocked || launching}
                onClick={() => (needsInputsStep ? setStep('inputs') : void startNow())}
              >
                {blocked ? 'Blocked — fix readiness first' : needsInputsStep ? 'Continue to inputs' : 'Start now'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
