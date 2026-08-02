import type {
  ReadinessItem,
  ReadinessLevel,
  ReadinessSummary,
  WorkflowPreflightReport,
} from '../../../api/types';

// Mirrors app/workflow/library_metadata.py's readiness_summary exactly —
// duplicated deliberately (it's ~10 lines) rather than round-tripping
// through the backend, since the Prepare-and-run flow needs to re-map a
// *fresh* WorkflowPreflightReport (from api.validateWorkflow) the instant a
// user changes an input, without a network round trip per keystroke.
export function readinessFromPreflight(report: WorkflowPreflightReport): ReadinessSummary {
  const errors = report.issues.filter(issue => issue.severity === 'error');
  const warnings = report.issues.filter(issue => issue.severity === 'warning');
  const level: ReadinessLevel = errors.length > 0
    ? 'blocked'
    : warnings.length > 0
      ? 'ready_with_warnings'
      : 'ready';
  const items: ReadinessItem[] = [...errors, ...warnings].map(issue => ({
    severity: issue.severity,
    code: issue.code,
    message: issue.message,
    suggestion: issue.suggestion ?? null,
  }));
  return { level, items };
}

export const READINESS_LABEL: Record<ReadinessLevel, string> = {
  ready: 'Ready',
  ready_with_warnings: 'Ready with warnings',
  blocked: 'Blocked',
};
