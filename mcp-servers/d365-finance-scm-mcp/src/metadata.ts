export interface ODataPropertyDescription {
  name: string;
  type: string;
  nullable: boolean;
}

export interface ODataEntityDescription {
  entitySet: string;
  entityType: string;
  keys: string[];
  properties: ODataPropertyDescription[];
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function extractEntitySets(metadataXml: string): string[] {
  const names = new Set<string>();
  const regex = /<EntitySet\s+Name="([A-Za-z_][A-Za-z0-9_.]*)"/g;
  for (const match of metadataXml.matchAll(regex)) {
    const name = match[1];
    if (name) names.add(name);
  }
  return [...names].sort((a, b) => a.localeCompare(b));
}

export function describeEntitySet(metadataXml: string, entitySet: string): ODataEntityDescription {
  const entitySetRegex = new RegExp(
    `<EntitySet\\s+Name="${escapeRegExp(entitySet)}"[^>]*EntityType="([^"]+)"[^>]*/?>`,
  );
  const entitySetMatch = entitySetRegex.exec(metadataXml);
  if (!entitySetMatch?.[1]) {
    throw new Error(`Entity set ${entitySet} was not found in OData metadata.`);
  }

  const qualifiedType = entitySetMatch[1];
  const typeName = qualifiedType.split(".").at(-1);
  if (!typeName) throw new Error(`Unable to resolve entity type for ${entitySet}.`);

  const entityTypeRegex = new RegExp(
    `<EntityType\\s+Name="${escapeRegExp(typeName)}"[^>]*>([\\s\\S]*?)<\\/EntityType>`,
  );
  const entityTypeMatch = entityTypeRegex.exec(metadataXml);
  if (!entityTypeMatch?.[1]) {
    throw new Error(`Entity type ${typeName} for ${entitySet} was not found in OData metadata.`);
  }

  const body = entityTypeMatch[1];
  const keys: string[] = [];
  const keyBlock = /<Key>([\s\S]*?)<\/Key>/.exec(body)?.[1] ?? "";
  for (const match of keyBlock.matchAll(/<PropertyRef\s+Name="([^"]+)"\s*\/>/g)) {
    if (match[1]) keys.push(match[1]);
  }

  const properties: ODataPropertyDescription[] = [];
  for (const match of body.matchAll(/<Property\s+Name="([^"]+)"\s+Type="([^"]+)"([^>]*)\/>/g)) {
    const name = match[1];
    const type = match[2];
    const tail = match[3] ?? "";
    if (!name || !type) continue;
    const nullableMatch = /Nullable="(true|false)"/.exec(tail);
    properties.push({
      name,
      type,
      nullable: nullableMatch ? nullableMatch[1] === "true" : true,
    });
  }

  return { entitySet, entityType: qualifiedType, keys, properties };
}
