import type { WorkflowPreflightReport } from '../../api/types';

export function PreflightPanel({
  report,
  validating,
  onValidate,
  onSelectNode,
  onAutofix,
  autofixing,
}: {
  report: WorkflowPreflightReport | null;
  validating: boolean;
  onValidate: () => void;
  onSelectNode: (nodeId: string) => void;
  onAutofix?: () => void;
  autofixing?: boolean;
}) {
  const errors = report?.issues.filter(issue => issue.severity === 'error') ?? [];
  const warnings = report?.issues.filter(issue => issue.severity === 'warning') ?? [];

  return (
    <div className="builder-inspector-scroll p-4">
      <div className="flex items-center justify-between gap-2">
        <div className="builder-panel-heading">Preflight</div>
        <div className="flex gap-2">
          {onAutofix && report && !report.valid && (
            <button
              className="ui-button ui-button--secondary"
              disabled={Boolean(autofixing)}
              onClick={onAutofix}
              type="button"
            >
              {autofixing ? 'Fixing…' : 'Auto-fix'}
            </button>
          )}
          <button
            className="ui-button ui-button--secondary"
            disabled={validating}
            onClick={onValidate}
            type="button"
          >
            {validating ? 'Checking…' : 'Run preflight'}
          </button>
        </div>
      </div>
      <p className="mt-1 text-xs leading-5 text-ink-500">
        Structural validation only — zero model tokens are spent. Errors
        block Save and Run; warnings do not.
      </p>

      {!report && !validating && (
        <div className="mt-4 rounded-md border border-dashed border-ink-200 p-4 text-center text-xs text-ink-500">
          No preflight has run yet for the current canvas.
        </div>
      )}

      {report && (
        <div className="mt-4 space-y-4">
          <div
            className={`rounded-lg border p-3 text-sm font-semibold ${
              report.valid
                ? 'border-emerald-300 bg-emerald-50 text-emerald-700'
                : 'border-red-300 bg-red-50 text-red-700'
            }`}
          >
            {report.valid ? 'Preflight passed' : `Preflight blocked: ${errors.length} error(s)`}
          </div>
          <div className="text-[11px] text-ink-500">
            {report.node_count} nodes · {report.edge_count} edges · {report.tokens_spent} tokens used
          </div>

          {report.checks.length > 0 && (
            <section>
              <div className="text-xs font-semibold text-ink-800">Checks</div>
              <ul className="mt-2 space-y-1.5">
                {report.checks.map(check => (
                  <li className="flex items-start gap-2 text-xs" key={check.name}>
                    <span
                      className={
                        check.status === 'passed'
                          ? 'text-emerald-600'
                          : check.status === 'failed'
                            ? 'text-red-600'
                            : 'text-amber-600'
                      }
                    >
                      {check.status === 'passed' ? '✓' : check.status === 'failed' ? '✗' : '•'}
                    </span>
                    <span className="text-ink-700">
                      <span className="font-medium">{check.name}</span> — {check.detail}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {errors.length > 0 && (
            <IssueList title="Errors" issues={errors} tone="error" onSelectNode={onSelectNode} />
          )}
          {warnings.length > 0 && (
            <IssueList title="Warnings" issues={warnings} tone="warning" onSelectNode={onSelectNode} />
          )}
        </div>
      )}
    </div>
  );
}

function IssueList({
  title,
  issues,
  tone,
  onSelectNode,
}: {
  title: string;
  issues: WorkflowPreflightReport['issues'];
  tone: 'error' | 'warning';
  onSelectNode: (nodeId: string) => void;
}) {
  return (
    <section>
      <div className={`text-xs font-semibold ${tone === 'error' ? 'text-red-700' : 'text-amber-700'}`}>
        {title} ({issues.length})
      </div>
      <ul className="mt-2 space-y-2">
        {issues.map((issue, index) => {
          const clickable = Boolean(issue.node_id);
          const content = (
            <>
              <span className="font-semibold">{issue.code}</span>
              {issue.node_id ? ` · ${issue.node_id}` : ''}
              {issue.path ? ` · ${issue.path}` : ''}
              {`: ${issue.message}`}
              {issue.suggestion ? ` ${issue.suggestion}` : ''}
            </>
          );
          return (
            <li key={`${issue.code}:${issue.path ?? index}`}>
              {clickable ? (
                <button
                  className={`w-full rounded-md border px-2 py-1.5 text-left text-xs hover:opacity-80 ${
                    tone === 'error'
                      ? 'border-red-200 bg-red-50 text-red-700'
                      : 'border-amber-200 bg-amber-50 text-amber-700'
                  }`}
                  onClick={() => onSelectNode(issue.node_id as string)}
                  type="button"
                >
                  {content}
                </button>
              ) : (
                <div
                  className={`rounded-md border px-2 py-1.5 text-xs ${
                    tone === 'error'
                      ? 'border-red-200 bg-red-50 text-red-700'
                      : 'border-amber-200 bg-amber-50 text-amber-700'
                  }`}
                >
                  {content}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
