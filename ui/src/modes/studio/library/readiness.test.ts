import { describe, expect, it } from 'vitest';
import type { WorkflowPreflightReport } from '../../../api/types';
import { readinessFromPreflight } from './readiness';

function report(issues: WorkflowPreflightReport['issues']): WorkflowPreflightReport {
  return {
    valid: !issues.some(issue => issue.severity === 'error'),
    node_count: 1,
    edge_count: 0,
    required_services: [],
    checks: [],
    issues,
    tokens_spent: 0,
  };
}

describe('readinessFromPreflight', () => {
  it('is ready with no items when there are no issues', () => {
    const readiness = readinessFromPreflight(report([]));
    expect(readiness).toEqual({ level: 'ready', items: [] });
  });

  it('is blocked when any error is present, even alongside warnings', () => {
    const readiness = readinessFromPreflight(report([
      { code: 'A', severity: 'warning', message: 'warn' },
      { code: 'B', severity: 'error', message: 'err' },
    ]));
    expect(readiness.level).toBe('blocked');
    expect(readiness.items.map(item => item.code)).toEqual(['B', 'A']);
  });

  it('is ready_with_warnings when only warnings are present', () => {
    const readiness = readinessFromPreflight(report([
      { code: 'A', severity: 'warning', message: 'warn' },
    ]));
    expect(readiness.level).toBe('ready_with_warnings');
  });

  it('preserves the suggestion field, defaulting to null', () => {
    const readiness = readinessFromPreflight(report([
      { code: 'A', severity: 'error', message: 'err', suggestion: 'fix it' },
      { code: 'B', severity: 'error', message: 'err2' },
    ]));
    expect(readiness.items[0].suggestion).toBe('fix it');
    expect(readiness.items[1].suggestion).toBeNull();
  });
});
