import { describe, expect, it } from 'vitest';

import {
  collectConditionFields,
  evaluateCondition,
  evaluateConditionGroup,
  validateFieldConstraints,
  type FormConditionGroup,
} from './formConditions';

describe('evaluateCondition', () => {
  it('matches equals case-insensitively for strings', () => {
    expect(evaluateCondition({ field: 'kind', operator: 'equals', value: 'Service' }, { kind: 'service' })).toBe(true);
  });

  it('matches not_equals', () => {
    expect(evaluateCondition({ field: 'kind', operator: 'not_equals', value: 'service' }, { kind: 'rfq' })).toBe(true);
    expect(evaluateCondition({ field: 'kind', operator: 'not_equals', value: 'service' }, { kind: 'service' })).toBe(false);
  });

  it('matches contains against an array', () => {
    expect(evaluateCondition(
      { field: 'request_types', operator: 'contains', value: 'complaint' },
      { request_types: ['rfq', 'complaint'] },
    )).toBe(true);
    expect(evaluateCondition(
      { field: 'request_types', operator: 'contains', value: 'complaint' },
      { request_types: ['rfq'] },
    )).toBe(false);
  });

  it('matches contains against a string as a substring check', () => {
    expect(evaluateCondition({ field: 'notes', operator: 'contains', value: 'urgent' }, { notes: 'this is Urgent' })).toBe(true);
  });

  it('matches in against a list of candidate values', () => {
    expect(evaluateCondition({ field: 'kind', operator: 'in', value: ['service', 'rfq'] }, { kind: 'rfq' })).toBe(true);
    expect(evaluateCondition({ field: 'kind', operator: 'in', value: ['service', 'rfq'] }, { kind: 'complaint' })).toBe(false);
  });

  it('treats a missing field as never matching equals', () => {
    expect(evaluateCondition({ field: 'missing', operator: 'equals', value: 'x' }, {})).toBe(false);
  });
});

describe('evaluateConditionGroup', () => {
  it('treats a missing or empty group as always visible', () => {
    expect(evaluateConditionGroup(undefined, {})).toBe(true);
    expect(evaluateConditionGroup({ operator: 'and', conditions: [] }, {})).toBe(true);
  });

  it('requires every condition to hold for and', () => {
    const group: FormConditionGroup = {
      operator: 'and',
      conditions: [
        { field: 'kind', operator: 'equals', value: 'service' },
        { field: 'stopped', operator: 'equals', value: true },
      ],
    };
    expect(evaluateConditionGroup(group, { kind: 'service', stopped: true })).toBe(true);
    expect(evaluateConditionGroup(group, { kind: 'service', stopped: false })).toBe(false);
  });

  it('requires only one condition to hold for or', () => {
    const group: FormConditionGroup = {
      operator: 'or',
      conditions: [
        { field: 'kind', operator: 'equals', value: 'service' },
        { field: 'kind', operator: 'equals', value: 'rfq' },
      ],
    };
    expect(evaluateConditionGroup(group, { kind: 'rfq' })).toBe(true);
    expect(evaluateConditionGroup(group, { kind: 'complaint' })).toBe(false);
  });

  it('negates its single condition for not', () => {
    const group: FormConditionGroup = {
      operator: 'not',
      conditions: [{ field: 'kind', operator: 'equals', value: 'service' }],
    };
    expect(evaluateConditionGroup(group, { kind: 'rfq' })).toBe(true);
    expect(evaluateConditionGroup(group, { kind: 'service' })).toBe(false);
  });

  it('supports a nested group as one of the conditions', () => {
    const group: FormConditionGroup = {
      operator: 'and',
      conditions: [
        { field: 'kind', operator: 'equals', value: 'service' },
        {
          operator: 'or',
          conditions: [
            { field: 'urgency', operator: 'equals', value: 'high' },
            { field: 'urgency', operator: 'equals', value: 'critical' },
          ],
        },
      ],
    };
    expect(evaluateConditionGroup(group, { kind: 'service', urgency: 'critical' })).toBe(true);
    expect(evaluateConditionGroup(group, { kind: 'service', urgency: 'low' })).toBe(false);
  });
});

describe('collectConditionFields', () => {
  it('returns every field referenced, including inside nested groups', () => {
    const group: FormConditionGroup = {
      operator: 'and',
      conditions: [
        { field: 'kind', operator: 'equals', value: 'service' },
        { operator: 'or', conditions: [{ field: 'urgency', operator: 'equals', value: 'high' }] },
      ],
    };
    expect(collectConditionFields(group)).toEqual(['kind', 'urgency']);
  });

  it('returns an empty list for a missing group', () => {
    expect(collectConditionFields(undefined)).toEqual([]);
  });
});

describe('validateFieldConstraints', () => {
  it('flags a value shorter than min_length', () => {
    const errors = validateFieldConstraints(
      [{ name: 'notes', type: 'text', min_length: 10 }],
      { notes: 'too short' },
    );
    expect(errors.notes).toMatch(/at least 10 characters/);
  });

  it('flags a value longer than max_length', () => {
    const errors = validateFieldConstraints(
      [{ name: 'notes', type: 'text', max_length: 5 }],
      { notes: 'way too long' },
    );
    expect(errors.notes).toMatch(/at most 5 characters/);
  });

  it('flags an invalid email format', () => {
    const errors = validateFieldConstraints(
      [{ name: 'email', type: 'string', format: 'email' }],
      { email: 'not-an-email' },
    );
    expect(errors.email).toMatch(/valid email/);
  });

  it('accepts a valid email format', () => {
    const errors = validateFieldConstraints(
      [{ name: 'email', type: 'string', format: 'email' }],
      { email: 'person@example.com' },
    );
    expect(errors.email).toBeUndefined();
  });

  it('flags a percentage outside the default 0-100 range', () => {
    const errors = validateFieldConstraints(
      [{ name: 'discount', type: 'number', format: 'percentage' }],
      { discount: 150 },
    );
    expect(errors.discount).toMatch(/between 0 and 100/);
  });

  it('honors a configured percentage range override', () => {
    const errors = validateFieldConstraints(
      [{ name: 'score', type: 'number', format: 'percentage', minimum: 1, maximum: 10 }],
      { score: 15 },
    );
    expect(errors.score).toMatch(/between 1 and 10/);
  });

  it('flags a date range whose end precedes its start', () => {
    const errors = validateFieldConstraints(
      [{ name: 'window', type: 'object', preset: 'date_range' }],
      { window: { start: '2026-05-10', end: '2026-05-01' } },
    );
    expect(errors.window).toMatch(/end date must be on or after/);
  });

  it('skips a field with no value entirely', () => {
    const errors = validateFieldConstraints(
      [{ name: 'email', type: 'string', format: 'email' }],
      {},
    );
    expect(errors.email).toBeUndefined();
  });
});
