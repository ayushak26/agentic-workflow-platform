import type { ContractField, ContractNode, OutputContract } from '../../../api/types';

/**
 * The field-matching/ranking core behind `FieldPicker`'s "Recommended"
 * shortlist — split out so a caller that wants a suggestion without opening
 * the picker UI (e.g. an MCP tool argument that has no value yet) can call
 * the same scoring logic directly. `FieldPicker.tsx` imports from here too;
 * this file owns the ranking, that file owns the browsing UI.
 */

export type DestinationKind = 'text' | 'number' | 'boolean' | 'any';

export function typeLabel(field: Pick<ContractField, 'type' | 'item_type'>): string {
  if (field.type === 'list' && field.item_type) return `List of ${field.item_type}`;
  if (field.type === 'unknown') return 'Untyped';
  return field.type.charAt(0).toUpperCase() + field.type.slice(1);
}

// A value's type doesn't have to match a destination exactly, just be safely
// usable there — a number reads fine wherever text is expected. `unknown`
// (an untyped upstream step) stays visible everywhere rather than being
// hidden on a guess.
const COMPATIBLE_TYPES: Record<Exclude<DestinationKind, 'any'>, Set<string>> = {
  text: new Set(['string', 'number', 'integer', 'boolean', 'unknown']),
  number: new Set(['number', 'integer', 'unknown']),
  boolean: new Set(['boolean', 'unknown']),
};

export function isTypeCompatible(field: ContractField, destinationKind: DestinationKind): boolean {
  if (destinationKind === 'any') return true;
  return COMPATIBLE_TYPES[destinationKind].has(field.type);
}

export function incompatibilityReason(field: ContractField, destinationKind: DestinationKind): string {
  return `${typeLabel(field)} — cannot be used directly as ${destinationKind}`;
}

// A small, hand-authored set of business-vocabulary synonyms used only to
// break ties in "Recommended" — not derived from real usage data, and never
// used to hide or gate anything, only to rank what's already shown.
const SYNONYMS: Record<string, string[]> = {
  message: ['email', 'body', 'content', 'text'],
  customer: ['client', 'account', 'requester', 'contact'],
  problem: ['issue', 'complaint', 'request', 'ticket'],
  subject: ['title', 'headline'],
  name: ['full_name', 'display_name'],
  category: ['type', 'classification', 'intent'],
  source: ['channel', 'origin'],
};

export function tokenize(text: string): string[] {
  const base = text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  const expanded = new Set(base);
  for (const token of base) {
    for (const [key, synonyms] of Object.entries(SYNONYMS)) {
      if (token === key) synonyms.forEach(item => expanded.add(item));
      if (synonyms.includes(token)) expanded.add(key);
    }
  }
  return [...expanded];
}

export function overlapCount(a: string[], b: string[]): number {
  const setB = new Set(b);
  return a.filter(token => setB.has(token)).length;
}

export function asContractField(item: OutputContract['inputs'][number]): ContractField {
  return {
    path: item.name,
    reference: item.reference,
    type: item.type,
    description: item.description,
    required: item.required,
    may_be_unavailable: !item.required,
    enum_values: [],
    item_type: null,
    operators: [],
  };
}

export type Candidate = { field: ContractField; node: ContractNode | null; score: number };

// Later index = declared closer to the selected step among its ancestors —
// the best recency proxy the backend contract exposes (see
// app/api/builder.py's output_contract(), which preserves the workflow's own
// declaration order). Workflow inputs get a flat mid-range score: they're
// always relevant, never more or less "recent" than any particular step.
export function scoreField(
  field: ContractField,
  recency: number,
  destinationTokens: string[],
  destinationKind: DestinationKind,
): number {
  if (!isTypeCompatible(field, destinationKind)) return -Infinity;
  const candidateTokens = tokenize(`${field.path} ${field.description}`);
  let score = 4 * overlapCount(candidateTokens, destinationTokens);
  score += 2 * recency;
  score += field.may_be_unavailable ? 0 : 1.5;
  return score;
}

export const RECOMMEND_MIN = 5;
export const RECOMMEND_MAX = 3;

export function buildRecommended(
  groups: Array<{ node: ContractNode; fields: ContractField[] }>,
  inputs: OutputContract['inputs'],
  destinationLabel: string | undefined,
  destinationHint: string | undefined,
  destinationKind: DestinationKind,
): Candidate[] {
  const destinationTokens = tokenize(`${destinationLabel ?? ''} ${destinationHint ?? ''}`);
  if (destinationTokens.length === 0) return [];

  const candidates: Candidate[] = [];
  const totalNodes = groups.length;
  groups.forEach(({ node, fields }, index) => {
    const recency = totalNodes > 1 ? index / (totalNodes - 1) : 1;
    for (const field of fields) {
      const score = scoreField(field, recency, destinationTokens, destinationKind);
      if (Number.isFinite(score) && score > 0) candidates.push({ field, node, score });
    }
  });
  for (const item of inputs) {
    const field = asContractField(item);
    const score = scoreField(field, 0.5, destinationTokens, destinationKind);
    if (Number.isFinite(score) && score > 0) candidates.push({ field, node: null, score });
  }

  if (candidates.length < RECOMMEND_MIN) return [];
  return candidates.sort((a, b) => b.score - a.score).slice(0, RECOMMEND_MAX);
}

/**
 * The single best suggestion for one destination field, across every node in
 * the contract plus workflow inputs — the same ranking `buildRecommended`
 * uses for the picker's shortlist, collapsed to its top pick. Returns `null`
 * when nothing scores (destination has no tokens to match against, or no
 * compatible candidate exists) — callers must treat that as "no suggestion",
 * never guess further.
 */
export function suggestField(
  contract: OutputContract | null,
  destinationLabel: string | undefined,
  destinationHint: string | undefined,
  destinationKind: DestinationKind,
): Candidate | null {
  if (!contract) return null;
  const groups = contract.nodes.map(node => ({ node, fields: node.fields }));
  const recommended = buildRecommended(groups, contract.inputs, destinationLabel, destinationHint, destinationKind);
  return recommended[0] ?? null;
}
