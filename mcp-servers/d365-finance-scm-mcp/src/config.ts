import { ConfigurationError } from "./errors.js";

export interface AppConfig {
  baseUrl: string;
  tenantId: string;
  clientId: string;
  clientSecret: string;
  allowWrites: boolean;
  allowDeletes: boolean;
  readEntityAllowlist: Set<string>;
  writeEntityAllowlist: Set<string>;
  deleteEntityAllowlist: Set<string>;
  timeoutMs: number;
  maxRetries: number;
  maxPageSize: number;
  maxPages: number;
  entityAliases: Record<string, string>;
}

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) {
    throw new ConfigurationError(`Missing required environment variable: ${name}`);
  }
  return value;
}

function parseBoolean(name: string, fallback = false): boolean {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  if (/^(true|1|yes)$/i.test(raw.trim())) return true;
  if (/^(false|0|no)$/i.test(raw.trim())) return false;
  throw new ConfigurationError(`${name} must be true/false, 1/0, or yes/no.`);
}

function parseInteger(name: string, fallback: number, min: number, max: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const value = Number.parseInt(raw, 10);
  if (!Number.isInteger(value) || value < min || value > max) {
    throw new ConfigurationError(`${name} must be an integer between ${min} and ${max}.`);
  }
  return value;
}

function parseCsvSet(name: string): Set<string> {
  const raw = process.env[name]?.trim();
  if (!raw) return new Set();
  return new Set(raw.split(",").map((value) => value.trim()).filter(Boolean));
}

function parseAliases(): Record<string, string> {
  const raw = process.env.FNO_ENTITY_ALIASES_JSON?.trim();
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch (error) {
    throw new ConfigurationError(`FNO_ENTITY_ALIASES_JSON must be valid JSON: ${String(error)}`);
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new ConfigurationError("FNO_ENTITY_ALIASES_JSON must be a JSON object of alias -> entity set.");
  }
  const result: Record<string, string> = {};
  for (const [alias, entity] of Object.entries(parsed)) {
    if (typeof entity !== "string" || !entity.trim()) {
      throw new ConfigurationError(`Alias ${alias} must map to a non-empty string entity set.`);
    }
    result[alias] = entity.trim();
  }
  return result;
}

export function loadConfig(): AppConfig {
  const rawBaseUrl = required("FNO_BASE_URL");
  let parsedBaseUrl: URL;
  try {
    parsedBaseUrl = new URL(rawBaseUrl);
  } catch {
    throw new ConfigurationError("FNO_BASE_URL must be a valid absolute URL.");
  }
  if (!/^https:$/.test(parsedBaseUrl.protocol)) {
    throw new ConfigurationError("FNO_BASE_URL must use HTTPS.");
  }
  const baseUrl = parsedBaseUrl.origin + parsedBaseUrl.pathname.replace(/\/+$/, "");
  if (/\/data$/i.test(baseUrl)) {
    throw new ConfigurationError("FNO_BASE_URL must be the environment root URL and must not include /data.");
  }

  const allowWrites = parseBoolean("FNO_ALLOW_WRITES", false);
  const allowDeletes = parseBoolean("FNO_ALLOW_DELETES", false);
  const readEntityAllowlist = parseCsvSet("FNO_READ_ENTITY_ALLOWLIST");
  const writeEntityAllowlist = parseCsvSet("FNO_WRITE_ENTITY_ALLOWLIST");
  const deleteEntityAllowlist = parseCsvSet("FNO_DELETE_ENTITY_ALLOWLIST");

  if (allowWrites && writeEntityAllowlist.size === 0) {
    throw new ConfigurationError("FNO_ALLOW_WRITES=true requires a non-empty FNO_WRITE_ENTITY_ALLOWLIST.");
  }
  if (allowDeletes && deleteEntityAllowlist.size === 0) {
    throw new ConfigurationError("FNO_ALLOW_DELETES=true requires a non-empty FNO_DELETE_ENTITY_ALLOWLIST.");
  }

  return {
    baseUrl,
    tenantId: required("FNO_TENANT_ID"),
    clientId: required("FNO_CLIENT_ID"),
    clientSecret: required("FNO_CLIENT_SECRET"),
    allowWrites,
    allowDeletes,
    readEntityAllowlist,
    writeEntityAllowlist,
    deleteEntityAllowlist,
    timeoutMs: parseInteger("FNO_TIMEOUT_MS", 20_000, 1_000, 120_000),
    maxRetries: parseInteger("FNO_MAX_RETRIES", 3, 0, 8),
    maxPageSize: parseInteger("FNO_MAX_PAGE_SIZE", 100, 1, 1_000),
    maxPages: parseInteger("FNO_MAX_PAGES", 10, 1, 100),
    entityAliases: parseAliases(),
  };
}
