/* JSON Schema values are deliberately open-ended at the Builder boundary. */
/* eslint-disable @typescript-eslint/no-explicit-any */
import type { NodeTypeManifest } from '../../api/types';

/** Walk a JSON Schema and produce a default value matching it.
 * Used when dropping a new node on the canvas — gives the user a sensible
 * starting config they can edit.
 */
export function generateDefaults(schema: any): any {
  if (!schema) return undefined;
  if (schema.default !== undefined) return schema.default;
  if (schema.type === 'object' && schema.properties) {
    const out: Record<string, any> = {};
    for (const [k, v] of Object.entries(schema.properties)) {
      const d = generateDefaults(v);
      if (d !== undefined) out[k] = d;
    }
    return out;
  }
  if (schema.type === 'array') return [];
  if (schema.type === 'boolean') return false;
  if (schema.type === 'integer' || schema.type === 'number') return 0;
  if (schema.type === 'string') {
    if (schema.enum && schema.enum.length > 0) return schema.enum[0];
    return '';
  }
  // anyOf/oneOf: pick the first non-null branch
  const variants = schema.anyOf ?? schema.oneOf ?? [];
  const nonNull = variants.find((v: any) => v.type !== 'null');
  if (nonNull) return generateDefaults(nonNull);
  return undefined;
}

/** Generate a node id unique within the current canvas. */
export function newNodeId(typeName: string, existingIds: Set<string>): string {
  const stem = typeName.toLowerCase().replace(/agent$/, '').replace(/[^a-z0-9]/g, '_');
  let n = 1;
  while (existingIds.has(`${stem}_${n}`)) n++;
  return `${stem}_${n}`;
}

/** Find a node manifest by type_name. */
export function findManifest(
  manifests: NodeTypeManifest[],
  typeName: string,
): NodeTypeManifest | undefined {
  return manifests.find(m => m.type_name === typeName);
}
