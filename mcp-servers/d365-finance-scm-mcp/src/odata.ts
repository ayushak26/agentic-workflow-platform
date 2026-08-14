import { AuthorizationError } from "./errors.js";

export type Primitive = string | number | boolean | null;

const IDENTIFIER = /^[A-Za-z_][A-Za-z0-9_]*$/;
const ENTITY_SET = /^[A-Za-z_][A-Za-z0-9_.]*$/;

export function assertEntitySetName(value: string): string {
  if (!ENTITY_SET.test(value)) {
    throw new Error(`Invalid OData entity set name: ${value}`);
  }
  return value;
}

export function assertFieldName(value: string): string {
  if (!IDENTIFIER.test(value)) {
    throw new Error(`Invalid OData field name: ${value}`);
  }
  return value;
}

export function odataLiteral(value: Primitive): string {
  if (value === null) return "null";
  if (typeof value === "string") return `'${value.replaceAll("'", "''")}'`;
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("OData numeric values must be finite.");
    return String(value);
  }
  return value ? "true" : "false";
}

export function buildKeyPredicate(key: Record<string, Exclude<Primitive, null>>): string {
  const entries = Object.entries(key);
  if (entries.length === 0) throw new Error("At least one entity key field is required.");
  return `(${entries.map(([field, value]) => `${assertFieldName(field)}=${odataLiteral(value)}`).join(",")})`;
}

export type FilterOperator = "eq" | "ne" | "gt" | "ge" | "lt" | "le";

export interface FilterCondition {
  field: string;
  operator: FilterOperator;
  value: Primitive;
}

export function buildFilter(conditions: FilterCondition[]): string | undefined {
  if (conditions.length === 0) return undefined;
  return conditions
    .map(({ field, operator, value }) => `${assertFieldName(field)} ${operator} ${odataLiteral(value)}`)
    .join(" and ");
}

export function buildQueryString(options: {
  select?: string[];
  filter?: FilterCondition[];
  orderBy?: Array<{ field: string; direction: "asc" | "desc" }>;
  top?: number;
  count?: boolean;
  crossCompany?: boolean;
}): string {
  const params = new URLSearchParams();
  if (options.select?.length) {
    params.set("$select", options.select.map(assertFieldName).join(","));
  }
  const filter = buildFilter(options.filter ?? []);
  if (filter) params.set("$filter", filter);
  if (options.orderBy?.length) {
    params.set(
      "$orderby",
      options.orderBy.map(({ field, direction }) => `${assertFieldName(field)} ${direction}`).join(","),
    );
  }
  if (options.top !== undefined) params.set("$top", String(options.top));
  if (options.count) params.set("$count", "true");
  if (options.crossCompany) params.set("cross-company", "true");
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function assertAllowedEntity(
  entitySet: string,
  allowlist: Set<string>,
  operation: "read" | "write" | "delete",
): void {
  assertEntitySetName(entitySet);
  if (allowlist.size > 0 && !allowlist.has(entitySet)) {
    throw new AuthorizationError(`${operation} access to entity ${entitySet} is not allowed by configuration.`);
  }
}

export function resolveEntityAlias(aliasOrEntity: string, aliases: Record<string, string>): string {
  return assertEntitySetName(aliases[aliasOrEntity] ?? aliasOrEntity);
}
