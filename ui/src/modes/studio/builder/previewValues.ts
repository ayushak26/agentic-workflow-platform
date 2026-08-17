/**
 * Turns a step's last real test/simulation output into the same dotted-path
 * shape `ContractField.path` uses, so the picker can show an author what a
 * value actually looked like last time this ran, not just its description.
 */

const MAX_PREVIEW_LENGTH = 140;

function summarize(value: unknown): string {
  if (typeof value === 'string') {
    const text = value.replace(/\s+/g, ' ').trim();
    return text.length > MAX_PREVIEW_LENGTH ? `${text.slice(0, MAX_PREVIEW_LENGTH - 1)}…` : text;
  }
  if (Array.isArray(value)) {
    const text = JSON.stringify(value);
    return text.length > MAX_PREVIEW_LENGTH ? `${text.slice(0, MAX_PREVIEW_LENGTH - 1)}…` : text;
  }
  return String(value);
}

/** Flattens one node's raw output into `{ "parsed.customer_problem": "…" }`. */
export function flattenOutput(
  output: Record<string, unknown> | null | undefined,
  maxDepth = 6,
): Record<string, string> {
  const out: Record<string, string> = {};
  const walk = (prefix: string, value: unknown, depth: number) => {
    if (value === null || value === undefined) return;
    // Arrays are leaves, not indexed — ContractField.path never encodes an
    // array index (only `item_type` describes the element shape), so
    // recursing into one would manufacture a path the contract can never
    // match back to.
    if (typeof value === 'object' && !Array.isArray(value) && depth < maxDepth) {
      for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        walk(prefix ? `${prefix}.${key}` : key, child, depth + 1);
      }
      return;
    }
    if (prefix) out[prefix] = summarize(value);
  };
  if (output) walk('', output, 0);
  return out;
}

/** node_id -> flattened path -> preview string, for every node that has run. */
export function buildPreviewValues(
  nodeRunOutputs: Record<string, Record<string, unknown>>,
): Record<string, Record<string, string>> {
  return Object.fromEntries(
    Object.entries(nodeRunOutputs).map(([nodeId, output]) => [nodeId, flattenOutput(output)]),
  );
}
