/**
 * Client-side twin of app/runtime/rules.py's evaluate_group, restricted to
 * the operators a Start-form's conditional fields actually need (§16 of the
 * form spec: "Support simple operators... do not create a general
 * expression language"). Evaluated against a form's OWN currently-entered
 * field values — never workflow graph state — so this never needs the
 * outputs/inputs/variables namespacing the backend engine also understands.
 *
 * Kept as a small, separate reimplementation rather than a shared library
 * with the Python engine — the same house pattern already used for
 * deriveInputsFromStartNode's Python/JS twins (yaml-bridge.ts) — since a
 * cross-language shared engine isn't worth the build complexity for four
 * operators.
 */

export type FormConditionOperator = 'equals' | 'not_equals' | 'contains' | 'in';

export type FormCondition = {
  field: string;
  operator: FormConditionOperator;
  value?: unknown;
};

export type FormConditionGroup = {
  operator: 'and' | 'or' | 'not';
  conditions: Array<FormCondition | FormConditionGroup>;
};

function isGroup(item: FormCondition | FormConditionGroup): item is FormConditionGroup {
  return Array.isArray((item as FormConditionGroup).conditions);
}

function valuesEqual(actual: unknown, expected: unknown): boolean {
  if (typeof actual === 'string' && typeof expected === 'string') {
    return actual.trim().toLowerCase() === expected.trim().toLowerCase();
  }
  if (typeof actual === 'number' && typeof expected === 'number') return actual === expected;
  if (typeof actual === 'boolean' || typeof expected === 'boolean') return actual === expected;
  return actual === expected;
}

function contains(haystack: unknown, needle: unknown): boolean {
  if (haystack == null) return false;
  if (Array.isArray(haystack)) {
    return haystack.some(item => valuesEqual(item, needle));
  }
  if (typeof haystack === 'string') {
    return haystack.toLowerCase().includes(String(needle).toLowerCase());
  }
  return false;
}

export function evaluateCondition(condition: FormCondition, values: Record<string, unknown>): boolean {
  const actual = values[condition.field];
  switch (condition.operator) {
    case 'equals':
      return valuesEqual(actual, condition.value);
    case 'not_equals':
      return !valuesEqual(actual, condition.value);
    case 'contains':
      return contains(actual, condition.value);
    case 'in':
      return Array.isArray(condition.value) && condition.value.some(option => valuesEqual(actual, option));
    default:
      return false;
  }
}

export function evaluateConditionGroup(
  group: FormConditionGroup | null | undefined,
  values: Record<string, unknown>,
): boolean {
  if (!group || !group.conditions.length) return true;
  const results = group.conditions.map(item => (
    isGroup(item) ? evaluateConditionGroup(item, values) : evaluateCondition(item, values)
  ));
  if (group.operator === 'and') return results.every(Boolean);
  if (group.operator === 'or') return results.some(Boolean);
  return !results[0];
}

// ---------------------------------------------------------------------------
// Client-side mirror of app/nodes/workflow_input.py's validate_field_constraints.
//
// Preflight ("the full zero-token test" RunDialog runs before launching)
// never executes a node — it's structural validation only — so a bad email/
// percentage/length would otherwise only surface as a node failure *after*
// the run has already started. Checking here first keeps §20's "both
// frontend and backend must validate" true in practice, not just in theory;
// the backend check stays authoritative (an author who edits a saved
// workflow's YAML by hand still gets validated at run()).
// ---------------------------------------------------------------------------

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const URL_RE = /^https?:\/\/\S+\.\S+/i;
const PHONE_RE = /^[+\d][\d\s().-]{5,}$/;

type ConstraintField = {
  name: string;
  label?: string;
  type: string;
  format?: string;
  preset?: string;
  min_length?: number;
  max_length?: number;
  pattern?: string;
  minimum?: number | null;
  maximum?: number | null;
};

function friendlyNumber(value: number): string {
  return String(value);
}

export function validateFieldConstraints(
  fields: ConstraintField[],
  values: Record<string, unknown>,
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const value = values[field.name];
    if (value == null || value === '') continue;
    const label = field.label || field.name;

    if ((field.type === 'string' || field.type === 'text') && typeof value === 'string') {
      if (field.min_length != null && value.length < field.min_length) {
        errors[field.name] = `${label} must be at least ${field.min_length} characters.`;
        continue;
      }
      if (field.max_length != null && value.length > field.max_length) {
        errors[field.name] = `${label} must be at most ${field.max_length} characters.`;
        continue;
      }
      if (field.pattern && !new RegExp(field.pattern).test(value)) {
        errors[field.name] = `${label} is not in the expected format.`;
        continue;
      }
      if (field.format === 'email' && !EMAIL_RE.test(value)) {
        errors[field.name] = `Please enter a valid email address for ${label}.`;
        continue;
      }
      if (field.format === 'url' && !URL_RE.test(value)) {
        errors[field.name] = `Please enter a valid website address for ${label}.`;
        continue;
      }
      if (field.format === 'phone' && !PHONE_RE.test(value)) {
        errors[field.name] = `Please enter a valid phone number for ${label}.`;
        continue;
      }
    }

    if (field.format === 'percentage' && typeof value === 'number') {
      const lower = field.minimum ?? 0;
      const upper = field.maximum ?? 100;
      if (value < lower || value > upper) {
        errors[field.name] = `${label} must be between ${friendlyNumber(lower)} and ${friendlyNumber(upper)}.`;
      }
    }

    if (field.preset === 'date_range' && value && typeof value === 'object') {
      const { start, end } = value as { start?: string; end?: string };
      if (start && end && end < start) {
        errors[field.name] = `${label}: the end date must be on or after the start date.`;
      }
    }
  }
  return errors;
}

/** Every field name a condition (recursively) references — used by the
 * Builder to only ever offer earlier fields as pickable (app/runtime/
 * preflight.py's _validate_start_fields mirrors this same "earlier field
 * only" rule server-side). */
export function collectConditionFields(group: FormConditionGroup | null | undefined): string[] {
  if (!group) return [];
  const found: string[] = [];
  for (const item of group.conditions) {
    if (isGroup(item)) found.push(...collectConditionFields(item));
    else found.push(item.field);
  }
  return found;
}
