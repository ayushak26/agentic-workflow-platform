import { describe, expect, it } from 'vitest';
import type { NodeRun } from '../../../api/types';
import {
  classifyArtifact, historicalNodeStatus, outputSummary, shortDuration, suggestedCorrectiveAction,
} from './node-render';

function makeNodeRun(overrides: Partial<NodeRun> = {}): NodeRun {
  return {
    node_id: 'n1',
    type_name: 'TransformAgent',
    status: 'completed',
    input: {},
    output: {},
    started_at: 1,
    ended_at: 2,
    duration_s: 1,
    error: null,
    ...overrides,
  };
}

describe('shortDuration', () => {
  it('formats sub-second durations in ms', () => {
    expect(shortDuration(0.48)).toBe('480ms');
  });
  it('formats durations at or above a second in seconds', () => {
    expect(shortDuration(2.4)).toBe('2.4s');
  });
  it('returns null for a missing duration', () => {
    expect(shortDuration(null)).toBeNull();
    expect(shortDuration(undefined)).toBeNull();
  });
});

describe('outputSummary', () => {
  it('summarizes an array as a record count', () => {
    expect(outputSummary([1, 2, 3])).toBe('3 records');
    expect(outputSummary([1])).toBe('1 record');
  });
  it('summarizes an object with a raw/answer/summary field by its first line', () => {
    expect(outputSummary({ raw: 'first line\nsecond line' })).toBe('first line');
  });
  it('falls back to a field count for a plain object', () => {
    expect(outputSummary({ a: 1, b: 2 })).toBe('2 fields');
  });
  it('returns null for null/empty output', () => {
    expect(outputSummary(null)).toBeNull();
    expect(outputSummary({})).toBeNull();
  });
});

describe('suggestedCorrectiveAction', () => {
  it('matches a known failure category', () => {
    expect(suggestedCorrectiveAction('Request timed out after 30s')).toMatch(/took too long/);
    expect(suggestedCorrectiveAction('429 Too Many Requests')).toMatch(/rate-limiting/);
  });
  it('returns null for an unrecognized error and for no error', () => {
    expect(suggestedCorrectiveAction('something bizarre happened')).toBeNull();
    expect(suggestedCorrectiveAction(null)).toBeNull();
  });
});

describe('historicalNodeStatus', () => {
  it('maps a recorded NodeRun status through NODE_RUN_STATUS_MAP', () => {
    expect(historicalNodeStatus('n1', { n1: makeNodeRun({ status: 'completed' }) }, true)).toBe('done');
    expect(historicalNodeStatus('n1', { n1: makeNodeRun({ status: 'failed' }) }, true)).toBe('failed');
    expect(historicalNodeStatus('n1', { n1: makeNodeRun({ status: 'reused' }) }, false)).toBe('reused');
  });

  it('labels an un-run node "skipped" only once the run has ended', () => {
    expect(historicalNodeStatus('never-ran', {}, true)).toBe('skipped');
    expect(historicalNodeStatus('never-ran', {}, false)).toBe('pending');
  });
});

describe('classifyArtifact', () => {
  it('classifies an image key as an image, not a generic file', () => {
    const result = classifyArtifact({ minio_key: 'workflows/run1/chart.png' });
    expect(result).toEqual({ key: 'workflows/run1/chart.png', extension: 'png', isImage: true, isFile: false });
  });
  it('classifies a non-image key as a generic file', () => {
    const result = classifyArtifact({ pdf_key: 'workflows/run1/proposal.pdf' });
    expect(result).toEqual({ key: 'workflows/run1/proposal.pdf', extension: 'pdf', isImage: false, isFile: true });
  });
  it('returns null when the output has no recognizable artifact key', () => {
    expect(classifyArtifact({ raw: 'just text' })).toBeNull();
  });
});
