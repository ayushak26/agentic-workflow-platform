import type { ContractField, ContractNode, OutputContract } from '../../../api/types';

/**
 * What a config field's raw stored value actually means to a human.
 *
 * The backend only ever sees a string — literal text or a `{{...}}`
 * reference — but an author thinks in terms of "this is connected to that
 * earlier step" or "I typed this myself." This turns the raw value into the
 * distinction that actually matters for the UI, once per read, instead of
 * every panel re-deriving it with its own regex.
 */
export type FieldBinding =
  | { kind: 'empty' }
  | { kind: 'literal'; value: string }
  | { kind: 'resolved'; field: ContractField; node: ContractNode | null }
  // A well-formed {{...}} reference that matches nothing in the current
  // contract — the upstream step or field it pointed to was renamed or
  // removed since this was set. Never silently downgraded to "literal": that
  // would invite an author to edit it as text and corrupt it byte by byte.
  | { kind: 'unresolved'; raw: string };

// Mirrors app/runtime/templating.py's TEMPLATE_RE, including the trailing
// `?` optional-reference marker — this must stay in sync with that pattern.
const REFERENCE_RE = /^\{\{\s*(.+?)\s*\??\s*\}\}$/;

export function resolveBinding(raw: unknown, contract: OutputContract | null): FieldBinding {
  if (raw === undefined || raw === null || raw === '') return { kind: 'empty' };
  if (typeof raw !== 'string') return { kind: 'literal', value: String(raw) };

  const match = raw.match(REFERENCE_RE);
  if (!match) return { kind: 'literal', value: raw };

  const path = match[1];
  const resolved = contract ? resolveAgainstContract(path, contract) : null;
  return resolved ?? { kind: 'unresolved', raw };
}

function resolveAgainstContract(
  path: string,
  contract: OutputContract,
): { kind: 'resolved'; field: ContractField; node: ContractNode | null } | null {
  if (path.startsWith('outputs.')) {
    const rest = path.slice('outputs.'.length);
    const dot = rest.indexOf('.');
    // Node ids are validated elsewhere as plain identifiers (no dots), so the
    // first segment after "outputs." is always the node id.
    const nodeId = dot === -1 ? rest : rest.slice(0, dot);
    const fieldPath = dot === -1 ? '' : rest.slice(dot + 1);
    const node = contract.nodes.find(item => item.node_id === nodeId);
    const field = node?.fields.find(item => item.path === fieldPath);
    return node && field ? { kind: 'resolved', field, node } : null;
  }

  if (path.startsWith('inputs.')) {
    const name = path.slice('inputs.'.length);
    const input = contract.inputs.find(item => item.name === name);
    return input
      ? {
        kind: 'resolved',
        node: null,
        field: {
          path: input.name,
          reference: input.reference,
          type: input.type,
          description: input.description,
          required: input.required,
          may_be_unavailable: !input.required,
          enum_values: [],
          item_type: null,
          operators: [],
        },
      }
      : null;
  }

  if (path.startsWith('variables.')) {
    const name = path.slice('variables.'.length);
    const variable = contract.variables.find(item => item.name === name);
    return variable
      ? {
        kind: 'resolved',
        node: null,
        field: {
          path: variable.name,
          reference: variable.reference,
          type: variable.type,
          description: '',
          required: true,
          may_be_unavailable: false,
          enum_values: [],
          item_type: null,
          operators: [],
        },
      }
      : null;
  }

  return null;
}

/** "Understand Message", or "Workflow Input" for an input/variable binding. */
export function stepLabelFor(binding: { node: ContractNode | null }): string {
  return binding.node ? binding.node.label : 'Workflow Input';
}
