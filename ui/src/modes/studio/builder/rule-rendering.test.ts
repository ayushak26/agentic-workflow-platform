import { describe, expect, it } from 'vitest';

import type { BusinessRule } from '../../../api/types';
import {
  coerceValue,
  isGroup,
  newGroup,
  stripBraces,
  toReference,
  valueToText,
} from './ConditionGroupEditor';
import { renderRule } from './RuleBuilder';

/**
 * The rule editor's non-visual logic.
 *
 * These are the parts where a mistake is silent rather than obvious: a
 * threshold stored as the string "0.8" instead of the number 0.8 looks
 * identical in the form and is then rejected by preflight as a type mismatch
 * the author never intended.
 */

describe('coerceValue', () => {
  it('reads a decimal threshold as a number', () => {
    expect(coerceValue('0.8', 'number')).toBe(0.8);
  });

  it('reads a whole number as a number', () => {
    expect(coerceValue('80', 'integer')).toBe(80);
  });

  it('reads true and false as booleans', () => {
    expect(coerceValue('true', 'boolean')).toBe(true);
    expect(coerceValue('false', 'boolean')).toBe(false);
  });

  it('leaves an enum value as text', () => {
    expect(coerceValue('technical_support', 'enum')).toBe('technical_support');
  });

  it('leaves text that merely starts with a digit as text', () => {
    // "15 m3/h" must not silently become 15: guessing a number out of a unit
    // string is exactly the ambiguity the rule engine refuses to resolve.
    expect(coerceValue('15 m3/h', 'string')).toBe('15 m3/h');
  });

  it('infers a number when no field type is known', () => {
    expect(coerceValue('0.64')).toBe(0.64);
  });

  it('keeps an empty value as an empty string', () => {
    expect(coerceValue('')).toBe('');
  });
});

describe('valueToText', () => {
  it('renders a list of alternatives as a comma-separated string', () => {
    expect(valueToText(['a', 'b'])).toBe('a, b');
  });

  it('renders a missing value as empty rather than "undefined"', () => {
    expect(valueToText(undefined)).toBe('');
    expect(valueToText(null)).toBe('');
  });

  it('renders false as "false", not as empty', () => {
    expect(valueToText(false)).toBe('false');
  });
});

describe('reference conversion', () => {
  it('strips template braces, because rules address values directly', () => {
    expect(stripBraces('{{outputs.extract.result.intent}}'))
      .toBe('outputs.extract.result.intent');
  });

  it('tolerates padding inside the braces', () => {
    expect(stripBraces('{{ outputs.extract.confidence }}'))
      .toBe('outputs.extract.confidence');
  });

  it('round-trips a path back into a reference', () => {
    const path = 'outputs.extract.result.intent';
    expect(stripBraces(toReference(path))).toBe(path);
  });

  it('produces nothing for an empty path', () => {
    expect(toReference('')).toBe('');
  });
});

describe('isGroup', () => {
  it('distinguishes a nested group from a leaf condition', () => {
    expect(isGroup(newGroup())).toBe(true);
    expect(isGroup({ field: 'a', operator: 'equals', value: 1 })).toBe(false);
  });
});

describe('renderRule', () => {
  it('renders a rule the way the author reads it', () => {
    const rule: BusinessRule = {
      name: 'Priority support',
      when: {
        operator: 'and',
        conditions: [
          { field: 'outputs.extract.result.intent', operator: 'equals', value: 'technical_support' },
          { field: 'outputs.extract.confidence', operator: 'greater_or_equal', value: 0.8 },
        ],
      },
      then: [{ field: 'route', operation: 'set', value: 'priority_support' }],
    };
    const rendered = renderRule(rule);
    expect(rendered).toContain('RULE  Priority support');
    expect(rendered).toContain('IF');
    expect(rendered).toContain('outputs.extract.result.intent equals "technical_support"');
    expect(rendered).toContain('AND');
    expect(rendered).toContain('THEN');
    expect(rendered).toContain('route = "priority_support"');
  });

  it('renders nested groups with their grouping visible', () => {
    const rule: BusinessRule = {
      name: 'Escalate',
      when: {
        operator: 'or',
        conditions: [
          { field: 'production_stopped', operator: 'is_true' },
          {
            operator: 'and',
            conditions: [
              { field: 'urgency', operator: 'equals', value: 'high' },
              { field: 'tier', operator: 'equals', value: 'strategic' },
            ],
          },
        ],
      },
      then: [{ field: 'priority', operation: 'set', value: 'critical' }],
    };
    const rendered = renderRule(rule);
    expect(rendered).toContain('OR');
    expect(rendered).toContain('(');
    expect(rendered).toContain(')');
    expect(rendered).toContain('urgency equals "high"');
  });

  it('renders a NOT group as a negation', () => {
    const rendered = renderRule({
      name: 'Not a complaint',
      when: {
        operator: 'not',
        conditions: [{ field: 'intent', operator: 'equals', value: 'complaint' }],
      },
      then: [{ field: 'handled', operation: 'set', value: true }],
    });
    expect(rendered).toContain('NOT');
  });

  it('renders an always-applies rule without an IF', () => {
    const rendered = renderRule({
      name: 'Otherwise',
      default: true,
      then: [{ field: 'checked', operation: 'set', value: true }],
    });
    expect(rendered).toContain('ALWAYS');
    expect(rendered).not.toContain('IF');
  });

  it('names a non-set operation explicitly', () => {
    const rendered = renderRule({
      name: 'Collect reasons',
      default: true,
      then: [{ field: 'reasons', operation: 'merge', value: 'low confidence' }],
    });
    expect(rendered).toContain('reasons merge "low confidence"');
  });

  it('renders an omitted value as null instead of "undefined"', () => {
    const rendered = renderRule({
      name: 'Clear',
      default: true,
      then: [{ field: 'note', operation: 'set' }],
    });
    expect(rendered).toContain('note = null');
  });
});

describe('newGroup', () => {
  it('starts with one condition so the editor is never empty', () => {
    expect(newGroup().conditions).toHaveLength(1);
  });

  it('defaults to AND', () => {
    expect(newGroup().operator).toBe('and');
  });
});
