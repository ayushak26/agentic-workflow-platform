import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

import { describe, expect, it } from 'vitest';

import { dumpYaml, parseYaml, reactFlowToYaml, yamlToReactFlow } from '../yaml-bridge';

// Vitest provides the CJS __dirname shim even in its jsdom environment
// (import.meta.url is an http:// URL there, so fileURLToPath cannot be
// used). This file is excluded from the browser-scoped tsconfig.app.json,
// so the shim does not leak into app type-checking.
const WORKFLOWS_DIR = join(__dirname, '../../../../../workflows');

/**
 * Every shipped workflow, run through the exact Builder round trip (load ->
 * canvas -> save), must come back byte-for-byte semantically identical.
 * `edges: null` normalising to `edges: []` is the one accepted difference —
 * the backend schema requires a list, so that's the Builder silently fixing
 * an invalid file, not losing data. Anything else here is a real field the
 * Builder would silently drop or corrupt on the next open-and-save.
 */
function normalise(workflow: Record<string, unknown>) {
  return { ...workflow, edges: workflow.edges ?? [] };
}

function roundTrip(yamlText: string) {
  const original = parseYaml(yamlText);
  const { nodes, edges } = yamlToReactFlow(original);
  const { nodes: _n, edges: _e, ...meta } = original;
  void _n; void _e;
  const rebuilt = reactFlowToYaml(meta, nodes, edges);
  return { original, rebuilt: parseYaml(dumpYaml(rebuilt)) };
}

describe('every shipped workflow round-trips through the Builder without drift', () => {
  const files = readdirSync(WORKFLOWS_DIR).filter(name => name.endsWith('.yaml'));

  for (const filename of files) {
    it(filename, () => {
      const text = readFileSync(join(WORKFLOWS_DIR, filename), 'utf8');
      const { original, rebuilt } = roundTrip(text);
      expect(normalise(rebuilt)).toEqual(normalise(original));
    });
  }
});
